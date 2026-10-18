"""未言诗集 · a small, dependency-free chat server (Python 3.10+)."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.cookies
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser
import relay_core as relay
import groups
import friends
from avatars import COLORS, normalize_avatar
from release_config import VERSION

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DEFAULT_DATA = Path.home() / "Library/Application Support/WeiyanPoetry/server" if sys.platform == 'darwin' else ROOT / "data"
DATA = Path(os.environ.get("WEIYAN_DATA_DIR", str(DEFAULT_DATA))).resolve()
PUBLIC = ROOT / "public"
DB_PATH = DATA / "weiyan.sqlite3"
COOKIE = "weiyan_session"
SESSION_DAYS = 30
MAX_BODY = 48 * 1024 * 1024
MAX_IMAGE = 5 * 1024 * 1024
ROOMS = [
    ("room:lobby", "-扉页-", "交流日常，分享文字与见闻。", "flower", "#ffe4ec"),
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect():
    db = sqlite3.connect(DB_PATH, timeout=15, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE NOT NULL,
          nickname TEXT NOT NULL, password TEXT NOT NULL, avatar TEXT NOT NULL,
          color TEXT NOT NULL, bio TEXT NOT NULL DEFAULT '', is_guest INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversations (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '', avatar TEXT NOT NULL DEFAULT 'flower',
          color TEXT NOT NULL DEFAULT '#ffe4ec', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS members (
          conversation_id TEXT NOT NULL REFERENCES conversations(id),
          user_id TEXT NOT NULL REFERENCES users(id), last_read_id INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (conversation_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS contacts (
          user_id TEXT NOT NULL REFERENCES users(id), peer_id TEXT NOT NULL REFERENCES users(id),
          PRIMARY KEY (user_id, peer_id)
        );
        CREATE INDEX IF NOT EXISTS session_user ON sessions(user_id);
        """)
        db.execute("INSERT OR IGNORE INTO users(id,username,nickname,password,avatar,color,bio,is_guest,created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   ("system", "xiaojian", "小笺", "!", "flower", "#ffe4ec", "替你收藏每一句温柔。", 0, now()))
        for cid, name, description, avatar, color in ROOMS:
            inserted = db.execute("INSERT OR IGNORE INTO conversations VALUES (?, 'room', ?, ?, ?, ?, ?)",
                                  (cid, name, description, avatar, color, now())).rowcount
        relay.initialize(db, DATA)
        groups.initialize(db)
        friends.initialize(db)
        db.execute("UPDATE conversations SET name='-扉页-' WHERE id='room:lobby'")
        db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))


def public_user(row):
    return {key: row[key] for key in ("id", "username", "nickname", "avatar", "color", "bio", "is_guest")}


def password_hash(password):
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()
    return f"pbkdf2$310000${salt}${value}"


def password_matches(password, stored):
    try:
        _, iterations, salt, value = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, value)
    except (ValueError, TypeError):
        return False


class ApiError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


class Events:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = {}
        self.typing_times = {}

    def online(self):
        with self.lock:
            return sorted({value[0] for value in self.clients.values()})

    def subscribe(self, uid, device_id=""):
        key, inbox = secrets.token_hex(16), queue.Queue(maxsize=200)
        with self.lock:
            self.clients[key] = (uid, inbox, device_id)
        self.publish({"type": "presence", "online": self.online()})
        return key, inbox

    def remove(self, key):
        with self.lock:
            self.clients.pop(key, None)
        self.publish({"type": "presence", "online": self.online()})

    def publish(self, event, recipients=None):
        with self.lock:
            for uid, inbox, device_id in list(self.clients.values()):
                if recipients is None or uid in recipients:
                    try:
                        inbox.put_nowait(event)
                    except queue.Full:
                        # Drop the oldest event and require a full resync after a slow connection.
                        try:
                            inbox.get_nowait()
                            inbox.put_nowait({"type": "resync"})
                        except (queue.Empty, queue.Full):
                            pass


EVENTS = Events()
RATE_LOCK = threading.Lock()
RATE_BUCKETS = defaultdict(deque)


def rate_limit(key, limit, seconds=60):
    stamp = time.monotonic()
    with RATE_LOCK:
        bucket = RATE_BUCKETS[key]
        while bucket and bucket[0] < stamp - seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise ApiError("操作有点快，稍等一会儿再试吧。", 429)
        bucket.append(stamp)
        if len(RATE_BUCKETS) > 10000:
            for item in list(RATE_BUCKETS):
                if not RATE_BUCKETS[item] or RATE_BUCKETS[item][-1] < stamp - 3600:
                    del RATE_BUCKETS[item]


def access(db, cid, uid):
    conv = db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
    if conv is None:
        raise ApiError("这个会话不存在。", 404)
    if conv['kind'] == 'group':
        groups.membership(db, cid, uid, ApiError)
    if conv["kind"] != "room" and not db.execute(
        "SELECT 1 FROM members WHERE conversation_id=? AND user_id=?", (cid, uid)
    ).fetchone():
        raise ApiError("你暂时无法查看这个会话。", 403)
    return conv


def recipients(db, conv):
    if conv["kind"] == "room":
        return None
    return {r[0] for r in db.execute("SELECT user_id FROM members WHERE conversation_id=?", (conv["id"],))}


def conversation_json(db, row, uid):
    item = dict(row)
    if row['kind'] == 'group':
        item['group'] = groups.summary(db, row['id'], uid)
    if row["kind"] == "direct":
        peer = db.execute("SELECT u.* FROM users u JOIN members mb ON u.id=mb.user_id "
                          "WHERE mb.conversation_id=? AND u.id!=?", (row["id"], uid)).fetchone()
        item.update(name=peer["nickname"], avatar=peer["avatar"], color=peer["color"],
                    description=peer["bio"] or "和你分享每一个小小的日常。", peer=public_user(peer))
    item["last_message"] = None
    item["unread"] = 0
    if row["kind"] == "direct":
        peer_read = db.execute("SELECT last_read_id FROM members WHERE conversation_id=? AND user_id!=?", (row["id"], uid)).fetchone()
        item["peer_last_read"] = peer_read[0] if peer_read else 0
    return item


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Weiyan/1.0"

    def log_message(self, fmt, *args):
        # Do not put message content, cookies, or search queries into logs.
        if args and str(args[0]).startswith(('"GET /api/events', '"GET /api/bootstrap')):
            return
        pass  # Administration exposes counters, never request bodies or access logs.

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                         "font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

    def json_response(self, obj, status=200, cookie=None):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(payload)

    def body(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError("请求格式不正确。", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError("请求长度不正确。")
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            raise ApiError("文件太大了，请选择 5 MB 以内的图片。", 413)
        try:
            obj = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("请求格式不正确。")
        if not isinstance(obj, dict):
            raise ApiError("请求格式不正确。")
        return obj

    def auth(self, db):
        try:
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie[COOKIE].value if COOKIE in cookie else ""
        except http.cookies.CookieError:
            token = ""
        row = db.execute("SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id "
                         "WHERE s.token_hash=? AND s.expires>? AND u.disabled=0", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise ApiError("先登录，再和大家说说话吧。", 401)
        return row

    def new_session(self, db, uid):
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                   (hashlib.sha256(token.encode()).hexdigest(), uid, time.time() + SESSION_DAYS * 86400))
        db.commit()
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_DAYS * 86400}"

    def check_origin(self):
        if self.headers.get("X-Weiyan-Client") != "web":
            raise ApiError("请从未言诗集页面操作。", 403)
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ("http", "https") or parsed.netloc != self.headers.get("Host"):
                raise ApiError("请求来源不正确。", 403)

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            params = parse_qs(parsed.query)
            if path == "/api/health":
                return self.json_response({"ok": True, "app": "未言诗集", "version": VERSION, "privacy": self.privacy()})
            if path.startswith("/api/") or path.startswith("/uploads/"):
                with connect() as db:
                    me = self.auth(db)
                    uid = me["id"]
                    if path == "/api/socket":
                        device_id = params.get('device_id', [''])[0]
                        if not db.execute('SELECT 1 FROM devices WHERE id=? AND user_id=? AND revoked=0', (device_id, uid)).fetchone():
                            raise ApiError('请先注册有效的加密设备。', 403)
                        origin = self.headers.get("Origin")
                        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                            raise ApiError("请求来源不正确。", 403)
                        if self.headers.get("Upgrade", "").lower() != "websocket":
                            raise ApiError("需要 WebSocket 连接。", 426)
                        return self.websocket(uid, device_id)
                    if path == "/api/bootstrap":
                        convs = db.execute("SELECT c.* FROM conversations c WHERE (c.kind='room' AND c.id='room:lobby') OR EXISTS "
                                           "(SELECT 1 FROM members m WHERE m.conversation_id=c.id AND m.user_id=?)", (uid,)).fetchall()
                        contacts = db.execute("SELECT u.* FROM users u JOIN contacts c ON u.id=c.peer_id WHERE c.user_id=? ORDER BY u.nickname", (uid,)).fetchall()
                        online = EVENTS.online()
                        online_users = [public_user(r) for r in db.execute("SELECT * FROM users WHERE id IN (" + ",".join("?" * len(online)) + ")", online)] if online else []
                        return self.json_response({"me": public_user(me), "conversations": [conversation_json(db, r, uid) for r in convs],
                                                   "contacts": [public_user(r) for r in contacts], "online": online, "online_users": online_users, "server_id": relay.setting(db, "server_id"), "privacy": relay.privacy(db), "group_invites": groups.invitations(db, uid), "friend_requests": friends.pending(db, uid), "outgoing_friend_requests": friends.outgoing(db, uid)})
                    if path == '/api/groups':
                        return self.json_response(groups.details(db, params.get('conversation_id', [''])[0], uid, sys.modules[__name__]))
                    if path == '/api/profiles':
                        access(db, params.get('conversation_id', [''])[0], uid)
                        ids = list(dict.fromkeys(params.get('ids', [''])[0].split(',')))
                        if len(ids) > 100 or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', item) for item in ids):
                            raise ApiError('用户列表格式不正确。')
                        rows = db.execute('SELECT * FROM users WHERE id IN (' + ','.join('?' * len(ids)) + ')', ids)
                        return self.json_response({'users': [public_user(row) for row in rows]})
                    if path == "/api/keys":
                        cid = params.get("conversation_id", [""])[0]
                        access(db, cid, uid)
                        if params.get('receiving', [''])[0] == '1':
                            return self.json_response({'devices': relay.receive_directory(db, cid, uid, params.get('device_id', [''])[0], ApiError)})
                        return self.json_response({"devices": relay.directory(db, cid)})
                    if path == "/api/inbox":
                        return self.json_response(relay.inbox(db, uid, params.get("device_id", [""])[0], ApiError, integer(params.get("after", ["0"])[0])))
                    if "/messages" in path or path == "/api/search" or path.startswith("/uploads/"):
                        raise ApiError("聊天记录与图片仅保存在客户端。", 410)
                    if path == "/api/users":
                        query = params.get("q", [""])[0].strip()[:40]
                        if len(query) < 1:
                            return self.json_response({"users": []})
                        if query.startswith('@'):
                            rows = db.execute("SELECT * FROM users WHERE id NOT IN (?, 'system') AND is_guest=0 AND disabled=0 AND username=? COLLATE NOCASE LIMIT 20", (uid, query[1:])).fetchall()
                            return self.json_response({'users': [public_user(r) for r in rows]})
                        term = "%" + escape_like(query) + "%"
                        rows = db.execute("SELECT * FROM users WHERE id NOT IN (?, 'system') AND is_guest=0 AND disabled=0 AND "
                                          "(username LIKE ? ESCAPE '\\' OR nickname LIKE ? ESCAPE '\\') LIMIT 20", (uid, term, term)).fetchall()
                        return self.json_response({"users": [public_user(r) for r in rows]})
                    raise ApiError("没有找到这个地址。", 404)
            if path == "/":
                return self.json_response({"app": "未言诗集服务端", "ok": True, "message": "服务正常。请打开客户端，填写此服务端地址。"})
            raise ApiError("没有找到这个地址。", 404)
        except ApiError as exc:
            self.json_response({"error": exc.message}, exc.status)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass
        except Exception as exc:
            self.server.last_error = type(exc).__name__
            self.json_response({"error": "小笺暂时走神了，请稍后再试。"}, 500)

    def do_POST(self):
        try:
            self.check_origin()
            path = urlsplit(self.path).path
            payload = self.body()
            with connect() as db:
                if path in ("/api/register", "/api/login", "/api/guest"):
                    rate_limit(("auth", self.client_address[0]), 25)
                    if path != "/api/login" and relay.setting(db, "registration") != "1":
                        raise ApiError("管理员已暂停新账号注册。", 403)
                    if path == "/api/guest":
                        nickname = clean_text(payload.get("nickname", "掇花者"), 16, "昵称")
                        user = (secrets.token_hex(12), "guest_" + secrets.token_hex(8), nickname,
                                "!", secrets.choice(["bunny", "cat", "bear", "flower", "moon"]), "#ffe4ec", "愿未尽之言，皆有回响。", 1, now())
                        db.execute("INSERT INTO users(id,username,nickname,password,avatar,color,bio,is_guest,created_at) VALUES (?,?,?,?,?,?,?,?,?)", user)
                        row = db.execute("SELECT * FROM users WHERE id=?", (user[0],)).fetchone()
                    elif path == "/api/register":
                        username = validate_username(payload.get("username"))
                        password = validate_password(payload.get("password"))
                        nickname = clean_text(payload.get("nickname"), 16, "昵称")
                        if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                            raise ApiError("这个账号名已经有人使用了，换一个试试吧。", 409)
                        user = (secrets.token_hex(12), username, nickname, password_hash(password), "bunny", "#ffe4ec", "愿未尽之言，皆有回响。", 0, now())
                        db.execute("INSERT INTO users(id,username,nickname,password,avatar,color,bio,is_guest,created_at) VALUES (?,?,?,?,?,?,?,?,?)", user)
                        row = db.execute("SELECT * FROM users WHERE id=?", (user[0],)).fetchone()
                    else:
                        username = clean_text(payload.get("username"), 32, "账号")
                        password = payload.get("password", "")
                        if not isinstance(password, str) or len(password) > 128:
                            raise ApiError("账号或密码不正确。", 401)
                        row = db.execute("SELECT * FROM users WHERE username=? AND is_guest=0 AND id!='system'", (username,)).fetchone()
                        if not row or row["disabled"] or not password_matches(password, row["password"]):
                            raise ApiError("账号或密码不正确。", 401)
                    cookie = self.new_session(db, row["id"])
                    return self.json_response({"me": public_user(row)}, cookie=cookie)
                me = self.auth(db)
                uid = me["id"]
                if path in ('/api/friends', '/api/contacts'):
                    rate_limit(('friend', uid), 40)
                    return self.json_response(friends.action(db, uid, payload, sys.modules[__name__]))
                if path == '/api/groups':
                    rate_limit(('groups', uid), 80)
                    return self.json_response(groups.action(db, uid, payload, sys.modules[__name__]))
                if path == "/api/logout":
                    cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
                    token = cookie[COOKIE].value if COOKIE in cookie else ""
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
                    return self.json_response({"ok": True}, cookie=f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
                if path == "/api/profile":
                    rate_limit(('profile', uid), 30)
                    nickname = clean_text(payload.get("nickname", me["nickname"]), 16, "昵称")
                    bio = clean_text(payload.get("bio", ""), 80, "签名", allow_empty=True)
                    avatar = payload.get("avatar", me["avatar"])
                    color = payload.get("color", me["color"])
                    try:
                        if avatar != me['avatar']:
                            avatar = normalize_avatar(avatar)
                    except ValueError as exc:
                        raise ApiError(str(exc)) from exc
                    if color not in COLORS:
                        raise ApiError("请从列表里选择颜色。")
                    if me["is_guest"] and payload.get("username"):
                        username = validate_username(payload.get("username"))
                        password = validate_password(payload.get("password"))
                        if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                            raise ApiError("这个账号名已经有人使用了。", 409)
                        db.execute("UPDATE users SET username=?,password=?,is_guest=0 WHERE id=?", (username, password_hash(password), uid))
                    db.execute("UPDATE users SET nickname=?,bio=?,avatar=?,color=? WHERE id=?", (nickname, bio, avatar, color, uid))
                    db.commit()
                    EVENTS.publish({"type": "profile"})
                    return self.json_response({"me": public_user(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())})
                if path == "/api/conversations":
                    peer_id = clean_text(payload.get("peer_id"), 64, "用户")
                    if peer_id == uid or not db.execute("SELECT 1 FROM users WHERE id=? AND id!='system'", (peer_id,)).fetchone():
                        raise ApiError("没有找到这位朋友。", 404)
                    cid = "dm:" + ":".join(sorted((uid, peer_id)))
                    db.execute("INSERT OR IGNORE INTO conversations(id,kind,created_at) VALUES (?,'direct',?)", (cid, now()))
                    for member in (uid, peer_id):
                        db.execute("INSERT OR IGNORE INTO members(conversation_id,user_id) VALUES (?,?)", (cid, member))
                    db.commit()
                    EVENTS.publish({"type": "conversations"}, {uid, peer_id})
                    return self.json_response({"conversation": conversation_json(db, db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone(), uid)})
                if path == "/api/read":
                    cid = clean_text(payload.get("conversation_id"), 128, "会话")
                    access(db, cid, uid)
                    last_id = integer(payload.get("last_id", 0))
                    maximum = db.execute("SELECT COALESCE(MAX(id),0) FROM receipts WHERE conversation_id=?", (cid,)).fetchone()[0]
                    actual = min(last_id, maximum)
                    db.execute("INSERT INTO members VALUES (?,?,?) ON CONFLICT(conversation_id,user_id) DO UPDATE SET last_read_id=MAX(last_read_id,excluded.last_read_id)", (cid, uid, actual))
                    db.commit()
                    conv = access(db, cid, uid)
                    audience = recipients(db, conv) if conv["kind"] == "direct" else {uid}
                    EVENTS.publish({"type": "read", "conversation_id": cid, "last_id": actual, "user_id": uid}, audience)
                    return self.json_response({"ok": True})
                if path == "/api/devices":
                    result = relay.register_device(db, uid, payload, ApiError)
                    db.commit()
                    EVENTS.publish({"type": "devices"})
                    return self.json_response(result)
                if path == "/api/relay":
                    rate_limit(("message", uid), 90)
                    return self.json_response(relay.relay(db, uid, payload, access, ApiError, EVENTS), 201)
                if path == "/api/received":
                    return self.json_response(relay.received(db, uid, payload, ApiError))
                if path in ("/api/messages", "/api/upload"):
                    raise ApiError("服务端仅接收加密信封，明文与图片请在客户端处理。", 410)
                if path == "/api/typing":
                    cid = clean_text(payload.get("conversation_id"), 128, "会话")
                    conv = access(db, cid, uid)
                    rate_limit(("typing", uid), 80)
                    EVENTS.publish({"type": "typing", "conversation_id": cid, "user_id": uid, "nickname": me["nickname"]}, recipients(db, conv))
                    return self.json_response({"ok": True})
                raise ApiError("没有找到这个地址。", 404)
        except ApiError as exc:
            self.json_response({"error": exc.message}, exc.status)
        except sqlite3.IntegrityError:
            self.json_response({"error": "这条记录已经存在，请刷新后再试。"}, 409)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass
        except Exception as exc:
            self.server.last_error = type(exc).__name__
            self.json_response({"error": "小笺暂时走神了，请稍后再试。"}, 500)

    def privacy(self):
        with connect() as db:
            return relay.privacy(db)

    def websocket(self, uid, device_id):
        from simple_websocket import Server, ConnectionClosed
        environ = {"werkzeug.socket": self.connection, "REQUEST_METHOD": "GET", "SERVER_PROTOCOL": "HTTP/1.1"}
        environ.update({"HTTP_" + name.upper().replace("-", "_"): value for name, value in self.headers.items()})
        ws = Server(environ, ping_interval=12, max_message_size=32768)
        self.close_connection = True
        key, inbox = EVENTS.subscribe(uid, device_id)
        checked = time.monotonic()
        try:
            ws.send(json.dumps({"type": "ready", "user_id": uid}))
            while ws.connected and not getattr(self.server, "stopping", threading.Event()).is_set():
                if time.monotonic() - checked > 10:
                    with connect() as db:
                        try:
                            self.auth(db)
                            if not db.execute('SELECT 1 FROM devices WHERE id=? AND user_id=? AND revoked=0', (device_id, uid)).fetchone():
                                raise ApiError('此加密设备已撤销。', 401)
                        except ApiError:
                            ws.send(json.dumps({"type": "expired"}))
                            break
                    checked = time.monotonic()
                for _ in range(40):
                    try:
                        ws.send(json.dumps(inbox.get_nowait(), ensure_ascii=False))
                    except queue.Empty:
                        break
                packet = ws.receive(timeout=0.08)
                if packet is None:
                    continue
                payload = {}
                try:
                    payload = json.loads(packet)
                    if not isinstance(payload, dict):
                        raise ApiError("消息格式不正确。")
                    if payload.get("op") == "ping":
                        ws.send(json.dumps({"type": "pong"}))
                    else:
                        raise ApiError("不支持的消息类型。")
                except (ApiError, ValueError, TypeError) as exc:
                    ws.send(json.dumps({"type": "nack", "client_id": payload.get("client_id") if isinstance(payload, dict) else None,
                                        "error": exc.message if isinstance(exc, ApiError) else "消息格式不正确。"}, ensure_ascii=False))
        except (ConnectionClosed, OSError):
            pass
        finally:
            EVENTS.remove(key)
            try:
                ws.close()
            except (ConnectionClosed, OSError):
                pass

    def file_response(self, target, mime=None, private=False):
        if not target.is_file():
            raise ApiError("文件不存在。", 404)
        payload = target.read_bytes()
        mime = mime or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.security_headers()
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime in ("application/javascript", "image/svg+xml") else ""))
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "private, max-age=3600" if private else "no-cache")
        self.end_headers()
        self.wfile.write(payload)


def clean_text(value, maximum, label, allow_empty=False):
    if not isinstance(value, str):
        raise ApiError(f"请填写{label}。")
    value = value.strip()
    if len(value) > maximum or (not allow_empty and not value):
        raise ApiError(f"{label}请填写 1–{maximum} 个字符。" if not allow_empty else f"{label}最多 {maximum} 个字符。")
    if any(ord(char) < 32 and char not in "\n\t\r" for char in value):
        raise ApiError(f"{label}包含不支持的字符。")
    return value


def integer(value):
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ApiError("数字格式不正确。")
    if result < 0 or result > 9223372036854775807:
        raise ApiError("数字超出范围。")
    return result


def validate_username(value):
    value = clean_text(value, 24, "账号")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", value) or value.lower().startswith("guest_") or value.lower() == "xiaojian":
        raise ApiError("账号需为 3–24 位字母、数字或下划线，请避开 guest_ 开头。")
    return value.lower()


def validate_password(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise ApiError("密码需要 8–128 个字符。")
    return value


def escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class ChatServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def service_actions(self):
        stamp = time.monotonic()
        if stamp - getattr(self, 'cleaned_at', 0) > 30:
            with connect() as db:
                relay.cleanup(db)
                db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            with connect() as db:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.cleaned_at = stamp

    def handle_error(self, request, client_address):
        self.last_error = "ConnectionInterrupted"


def main():
    parser = argparse.ArgumentParser(description="未言诗集加密消息服务")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--headless", action="store_true", help="无界面服务模式")
    parser.add_argument("--browser", action="store_true", help="浏览器预览管理界面")
    parser.add_argument("--admin-port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    initialize()
    with connect() as db:
        args.host = args.host or relay.setting(db, 'listen_host', '0.0.0.0')
        args.port = args.port or int(relay.setting(db, 'listen_port', '23074'))
    if not args.headless:
        from admin import launch
        return launch(sys.modules[__name__], args)
    server = ChatServer((args.host, args.port), Handler)
    server.stopping = threading.Event()
    print(f"Encrypted relay: {args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "startup-error.txt").write_text(type(error).__name__ + ": " + str(error), encoding="utf-8")
        if getattr(sys, "frozen", False) and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "未言诗集服务端", 16)
        else:
            raise
