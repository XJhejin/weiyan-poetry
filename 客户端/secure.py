"""Client-only key custody, authenticated encryption, and encrypted local history.

The relay never imports this module. Network packets use libsodium SecretBox,
sealed boxes for recipient key wrapping, and Ed25519 signatures. All private
material and history stay in a Windows-DPAPI-protected local vault.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import quote, urlsplit

from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.secret import SecretBox
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import CryptoError, BadSignatureError

DOMAIN = b"WeiyanPoetry/envelope/v2\0"
DEVICE_DOMAIN = b"WeiyanPoetry/device/v2\0"
MAX_FILE = 20 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def b64(raw):
    return base64.b64encode(raw).decode("ascii")


def unb64(value):
    return base64.b64decode(value, validate=True)


def validate_image(image):
    if not isinstance(image, dict) or not isinstance(image.get('data'), str):
        raise SecurityError('图片格式不正确。')
    raw = unb64(image['data'])
    if len(raw) > 5 * 1024 * 1024:
        raise SecurityError('请选择 5 MB 以内的图片。')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif raw.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif raw.startswith((b'GIF87a', b'GIF89a')):
        mime = 'image/gif'
    elif len(raw) > 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        mime = 'image/webp'
    else:
        raise SecurityError('收到不支持的图片格式。')
    return {'data': b64(raw), 'mime': mime}


def validate_file(file):
    if not isinstance(file, dict) or not isinstance(file.get('data'), str):
        raise SecurityError('文件格式不正确。')
    raw = unb64(file['data'])
    name = file.get('name')
    if len(raw) > MAX_FILE or not isinstance(name, str) or not 1 <= len(name) <= 180 or any(ord(c) < 32 or c in '/\\' for c in name):
        raise SecurityError('文件名无效，或文件超过 20 MB。')
    kind, mime = 'file', 'application/octet-stream'
    if file.get('kind') == 'video':
        if len(raw) >= 12 and raw[4:8] == b'ftyp':
            kind, mime = 'video', 'video/mp4'
        elif raw.startswith(b'\x1aE\xdf\xa3'):
            kind, mime = 'video', 'video/webm'
        else:
            raise SecurityError('视频预览支持 MP4 和 WebM 格式，请将其他格式作为普通文件发送。')
    return {'data': b64(raw), 'name': name, 'size': len(raw), 'kind': kind, 'mime': mime}


class SecurityError(ValueError):
    pass


class RemoteError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def protect(raw, decrypt=False):
    """Use the platform account key; never fall back to an unprotected key."""
    if os.environ.get("WEIYAN_PLATFORM") == "android":
        from java import jclass
        return bytes(jclass("com.weiyan.poetry.KeyVault").protect(raw, decrypt))
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), buffer)
    dest = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "Weiyan Poetry local vault", None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise SecurityError("无法解锁本机加密数据。请使用原来的 Windows 账号与电脑。")
    try:
        return ctypes.string_at(dest.pbData, dest.cbData)
    finally:
        kernel.LocalFree(dest.pbData)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Vault:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        key_file = self.path / "key.dpapi"
        if key_file.exists():
            key = protect(key_file.read_bytes(), decrypt=True)
        else:
            key = secrets.token_bytes(32)
            # Exclusive creation prevents two windows from replacing an existing key.
            temporary = self.path / (secrets.token_hex(12) + '.tmp')
            try:
                with temporary.open('xb') as file:
                    file.write(protect(key))
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    # Windows rename fails if the destination exists and also
                    # works on exFAT, where hard links are unavailable.
                    if os.name == 'nt':
                        os.rename(temporary, key_file)
                    else:
                        os.link(temporary, key_file)
                except FileExistsError:
                    key = protect(key_file.read_bytes(), decrypt=True)
            finally:
                temporary.unlink(missing_ok=True)
        self.box = SecretBox(key)
        self.db_path = self.path / "history.sqlite3"
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS records(kind TEXT,key TEXT,value BLOB NOT NULL,PRIMARY KEY(kind,key));
            CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,cid TEXT NOT NULL,sender TEXT NOT NULL,client_id TEXT NOT NULL,value BLOB NOT NULL,UNIQUE(sender,client_id));
            CREATE INDEX IF NOT EXISTS history_cid ON history(cid,id);
            """)

    def db(self):
        return sqlite3.connect(self.db_path, timeout=20, factory=ClosingConnection)

    def pack(self, kind, key, value):
        return bytes(self.box.encrypt(canonical({"kind": kind, "key": str(key), "value": value})))

    def unpack(self, kind, key, raw):
        data = json.loads(self.box.decrypt(raw))
        if data["kind"] != kind or data["key"] != str(key):
            raise SecurityError("本机加密记录校验失败。")
        return data["value"]

    def get(self, kind, key, default=None):
        with self.lock, self.db() as db:
            row = db.execute("SELECT value FROM records WHERE kind=? AND key=?", (kind, str(key))).fetchone()
            return self.unpack(kind, key, row[0]) if row else default

    def put(self, kind, key, value):
        with self.lock, self.db() as db:
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, str(key), self.pack(kind, key, value)))

    def delete(self, kind, key):
        with self.lock, self.db() as db:
            db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, str(key)))

    def preferences(self):
        with self.lock, self.db() as db:
            return {k: self.unpack("ui", k, v) for k, v in db.execute("SELECT key,value FROM records WHERE kind='ui'")}

    def save_message(self, message):
        with self.lock, self.db() as db:
            old = db.execute("SELECT id FROM history WHERE sender=? AND client_id=?", (message["sender"]["id"], message["client_id"])).fetchone()
            if old:
                return False
            db.execute("INSERT INTO history VALUES (?,?,?,?,?)", (message["id"], message["conversation_id"], message["sender"]["id"], message["client_id"], self.pack("message", message["id"], message)))
            return True

    def message(self, sender, client_id):
        with self.lock, self.db() as db:
            row = db.execute("SELECT id,value FROM history WHERE sender=? AND client_id=?", (sender, client_id)).fetchone()
            return self.unpack("message", row[0], row[1]) if row else None

    def messages(self, cid=None, before=9223372036854775807, search=None, limit=51):
        with self.lock, self.db() as db:
            rows = db.execute("SELECT id,value FROM history WHERE id<?" + (" AND cid=?" if cid else "") + " ORDER BY id DESC", (before, cid) if cid else (before,))
            result = []
            for mid, value in rows:
                message = self.unpack("message", mid, value)
                if search is None or search.casefold() in message.get("body", "").casefold():
                    result.append(message)
                    if len(result) >= limit:
                        break
            return result

    def summary(self, conv, uid):
        cid = conv["id"]
        read = self.get("read", cid, 0)
        with self.lock, self.db() as db:
            count = db.execute("SELECT COUNT(*) FROM history WHERE cid=? AND id>? AND sender!=?", (cid, read, uid)).fetchone()[0]
        last = self.messages(cid, limit=1)
        conv.update(last_message=last[0] if last else None, unread=count)
        return conv


class SecureClient:
    def __init__(self, profile, server_url, cookie, bootstrap):
        self.url, self.cookie = server_url, cookie
        self.me = bootstrap["me"]
        self.server_id = bootstrap["server_id"]
        identity = hashlib.sha256((self.server_id + ":" + self.me["id"]).encode()).hexdigest()
        self.vault = Vault(Path(profile) / "secure" / identity)
        self.lock = threading.RLock()
        self.receive_lock = threading.Lock()
        self.poll_time = 0
        self.blocked = None
        keys = self.vault.get("identity", "keys")
        if keys is None:
            keys = {"device_id": secrets.token_hex(16), "box": b64(bytes(PrivateKey.generate())), "sign": b64(bytes(SigningKey.generate()))}
            self.vault.put("identity", "keys", keys)
        self.device_id = keys["device_id"]
        self.private = PrivateKey(unb64(keys["box"]))
        self.signing = SigningKey(unb64(keys["sign"]))
        device = {"device_id": self.device_id, "box_key": b64(bytes(self.private.public_key)), "sign_key": b64(bytes(self.signing.verify_key)), "name": "安卓客户端" if os.environ.get('WEIYAN_PLATFORM') == 'android' else "Windows 客户端"}
        proof = b64(self.signing.sign(DEVICE_DOMAIN + canonical(device)).signature)
        if not bootstrap.get('offline'):
            self.remote("/api/devices", {**device, "proof": proof})
        self.import_legacy(profile)

    def remote(self, path, data=None):
        parsed = urlsplit(self.url)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = cls(parsed.hostname, parsed.port, timeout=20)
        try:
            conn.request("POST" if data is not None else "GET", path, body=canonical(data) if data is not None else None,
                         headers={"Cookie": self.cookie, "Origin": self.url, "X-Weiyan-Client": "web", "Content-Type": "application/json"})
            response = conn.getresponse()
            value = json.loads(response.read())
            if response.status >= 400:
                raise RemoteError(value.get("error", "服务端暂时无法响应。"), response.status)
            return value
        except (OSError, http.client.HTTPException) as error:
            raise RemoteError("连接暂时断开了，小笺已留在本机。", 503) from error
        finally:
            conn.close()

    @staticmethod
    def fingerprint(device):
        digest = hashlib.sha256(unb64(device["box_key"]) + unb64(device["sign_key"])).hexdigest().upper()
        return " ".join(digest[i:i+4] for i in range(0, len(digest), 4))

    def directory(self, cid, approve=False, expected=None, receiving=False):
        suffix = '&receiving=1&device_id=' + self.device_id if receiving else ''
        directory = self.remote("/api/keys?conversation_id=" + quote(cid) + suffix)["devices"]
        groups = {}
        for device in directory:
            groups.setdefault(device["user_id"], {})[device["device_id"]] = self.fingerprint(device)
        digest = hashlib.sha256(canonical(groups)).hexdigest()
        if approve and expected != digest:
            raise SecurityError("设备列表刚刚发生变化，请重新核对后确认。")
        changed = []
        for uid, current in groups.items():
            pinned = self.vault.get("trust", uid)
            if pinned is None:
                self.vault.put("trust", uid, current)
            elif any(pinned.get(did) != fingerprint for did, fingerprint in current.items()):
                changed.append(uid)
                if approve:
                    self.vault.put("trust", uid, current)
        self.blocked = {"conversation_id": cid, "users": changed} if changed and not approve else None
        return directory, changed if not approve else [], digest

    def security(self, cid=None, approve=False, expected=None):
        directory, changed, digest = self.directory(cid, approve, expected, receiving=True) if cid else ([], [], "")
        policy = self.remote("/api/health").get("privacy", {})
        return {"encrypted": True, "local_encrypted": True, "algorithm": "libsodium · XSalsa20-Poly1305 / X25519 / Ed25519", "device_id": self.device_id,
                "fingerprint": self.fingerprint({"box_key": b64(bytes(self.private.public_key)), "sign_key": b64(bytes(self.signing.verify_key))}),
                "devices": [{**d, "fingerprint": self.fingerprint(d), "changed": d["user_id"] in changed} for d in directory],
                "key_changed": bool(changed), "directory_digest": digest, "privacy": policy}

    def send(self, data):
        with self.lock:
            cid, client_id = data.get("conversation_id"), data.get("client_id")
            if not isinstance(cid, str) or not isinstance(client_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", client_id):
                raise ValueError("消息编号无效。")
            existing = self.vault.message(self.me["id"], client_id)
            if existing:
                if existing["conversation_id"] != cid:
                    raise ValueError("消息编号与会话不匹配。")
                return existing
            body = data.get("body", "")
            if not isinstance(body, str) or len(body) > 2000:
                raise ValueError("消息最多 2000 个字符。")
            image_id = data.get("image_id")
            image = self.vault.get("image", image_id) if image_id else None
            file_id = data.get('file_id')
            attachment = self.vault.get('file', file_id) if file_id else None
            if file_id and not attachment:
                raise ValueError('文件无法发送，请重新选择。')
            if image and attachment:
                raise ValueError('每条消息请选择一项附件。')
            if image_id and not image:
                raise ValueError("图片无法发送，请重新选择。")
            if not body.strip() and not image and not attachment:
                raise ValueError("写点什么，再寄出这封小笺吧。")
            directory, changed, _ = self.directory(cid)
            if changed:
                raise SecurityError("联系人设备发生变化，发送已暂停。请点击加密标识核对安全指纹。")
            if cid.startswith("dm:") and not any(d["user_id"] != self.me["id"] for d in directory):
                raise SecurityError("对方尚未建立加密设备，请让对方先登录客户端。")
            pending = self.vault.get("pending", client_id)
            if pending and pending["packet"]["conversation_id"] != cid:
                raise ValueError("消息编号与会话不匹配。")
            if pending is None:
                created = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
                clear = {"body": body, "image": image, "sender": self.me, "created_at": created}
                if attachment:
                    clear['attachment'] = attachment
                if cid.startswith('group:'):
                    group = self.remote('/api/groups?conversation_id=' + quote(cid))['conversation']['group']
                    if group['nickname']:
                        clear['sender'] = {**self.me, 'nickname': group['nickname']}
                key = secrets.token_bytes(32)
                packet = {"v": 2, "conversation_id": cid, "sender_id": self.me["id"], "device_id": self.device_id, "client_id": client_id,
                          "ciphertext": b64(bytes(SecretBox(key).encrypt(canonical(clear)))),
                          "keys": {d["device_id"]: b64(SealedBox(PublicKey(unb64(d["box_key"]))).encrypt(key)) for d in directory}}
                packet["signature"] = b64(self.signing.sign(DOMAIN + canonical(packet)).signature)
                pending = {"packet": packet, "clear": clear}
                self.vault.put("pending", client_id, pending)
            try:
                response = self.remote("/api/relay", {"envelope": pending["packet"]})
            except RemoteError as error:
                if error.status == 409 and '设备列表' in str(error):
                    self.vault.delete('pending', client_id)
                raise
            clear = pending["clear"]
            message = {"id": response["id"], "conversation_id": cid, "client_id": client_id, "body": clear["body"], "sender": clear["sender"],
                       "created_at": clear["created_at"], "image": f"/local-images/{image_id}" if image_id else None, "encrypted": True}
            if clear.get('attachment'):
                message['attachment'] = {k: v for k, v in clear['attachment'].items() if k != 'data'}
                message['attachment']['url'] = '/local-files/' + file_id
            self.vault.save_message(message)
            self.vault.delete("pending", client_id)
            return message

    def receive(self):
        with self.receive_lock:
            result, after, cache = [], 0, {}
            for _ in range(32):
                messages, after, count = self._receive_page(after, cache)
                result.extend(messages)
                if count < 12:
                    break
            return result

    def _receive_page(self, after, cache):
            result = []
            inbox = self.remote("/api/inbox?device_id=" + self.device_id + "&after=" + str(after))
            for item in inbox["items"]:
                packet = item["envelope"]
                cid = packet["conversation_id"]
                try:
                    old = self.vault.message(packet["sender_id"], packet["client_id"])
                    if old:
                        self.remote("/api/received", {"device_id": self.device_id, "id": item["id"]})
                        continue
                    if cid not in cache:
                        cache[cid] = self.directory(cid, receiving=True)
                    directory, changed, _ = cache[cid]
                    if packet["sender_id"] in changed:
                        raise SecurityError("联系人设备发生变化，请核对安全指纹。")
                    sender_device = next((d for d in directory if d["device_id"] == packet["device_id"] and d["user_id"] == packet["sender_id"]), None)
                    if not sender_device:
                        raise SecurityError("发送设备已撤销，无法验证这条消息。")
                    signed = {k: v for k, v in packet.items() if k != "signature"}
                    VerifyKey(unb64(sender_device["sign_key"])).verify(DOMAIN + canonical(signed), unb64(packet["signature"]))
                    key = SealedBox(self.private).decrypt(unb64(packet["keys"][self.device_id]))
                    clear = json.loads(SecretBox(key).decrypt(unb64(packet["ciphertext"])))
                    if clear["sender"]["id"] != packet["sender_id"] or not isinstance(clear["body"], str) or len(clear["body"]) > 2000:
                        raise SecurityError("消息身份校验失败。")
                    image_url = None
                    if clear.get("image"):
                        image_id = hashlib.sha256((packet["sender_id"] + ":" + packet["client_id"]).encode()).hexdigest()
                        self.vault.put("image", image_id, validate_image(clear["image"]))
                        image_url = "/local-images/" + image_id
                    attachment = None
                    if clear.get('attachment'):
                        file_id = hashlib.sha256((packet['sender_id'] + ':' + packet['client_id']).encode()).hexdigest()
                        file = validate_file(clear['attachment'])
                        self.vault.put('file', file_id, file)
                        attachment = {k: v for k, v in file.items() if k != 'data'}
                        attachment['url'] = '/local-files/' + file_id
                    message = {"id": item["id"], "client_id": packet["client_id"], "conversation_id": cid, "body": clear["body"], "sender": clear["sender"],
                               "created_at": clear["created_at"], "image": image_url, "encrypted": True}
                    if attachment:
                        message['attachment'] = attachment
                    if self.vault.save_message(message):
                        result.append(message)
                        callback = getattr(self, 'on_received', None)
                        if callback:
                            callback(message)
                    # Acknowledge only after the encrypted SQLite transaction commits.
                    self.remote("/api/received", {"device_id": self.device_id, "id": item["id"]})
                except (CryptoError, BadSignatureError, KeyError, TypeError, ValueError) as error:
                    self.blocked = {"conversation_id": cid, "error": str(error)}
                    # Keep the ciphertext queued; an unverified message never enters history.
            items = inbox['items']
            return result, items[-1]['id'] if items else after, len(items)

    def bootstrap(self, data):
        self.me = data["me"]
        try:
            self.receive()
        except RemoteError as error:
            if error.status != 503:
                raise
        data["conversations"] = [self.vault.summary(conv, self.me["id"]) for conv in data["conversations"]]
        data["local_storage"] = self.vault.preferences()
        data["security"] = {"encrypted": True, "local_encrypted": True, "blocked": self.blocked}
        return data

    def import_legacy(self, profile):
        default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WeiyanPoetry" / "client" / "legacy"
        for folder in {Path(profile) / "legacy", default}:
            if not folder.exists():
                continue
            for file in folder.glob("*.dpapi"):
                marker = hashlib.sha256(file.read_bytes()).hexdigest()
                if self.vault.get("legacy", marker):
                    continue
                archive = json.loads(protect(file.read_bytes(), decrypt=True))
                if archive["server_id"] != self.server_id:
                    continue
                allowed = set(archive["members"].get(self.me["id"], []))
                for message in archive["messages"]:
                    if not message["conversation_id"].startswith("room:") and message["conversation_id"] not in allowed:
                        continue
                    message = dict(message)
                    if message.get("image"):
                        image_id = message["image"].rsplit("/", 1)[-1]
                        if image_id in archive["images"]:
                            self.vault.put("image", image_id, archive["images"][image_id])
                            message["image"] = "/local-images/" + image_id
                        else:
                            message["image"] = None
                    message["encrypted"] = True
                    self.vault.save_message(message)
                self.vault.put("legacy", marker, True)
