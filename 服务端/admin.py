"""Loopback-only administrative UI and lifecycle controller for the relay."""
import hmac
import errno
import http.cookies
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import webbrowser
from update import Updater
from listening import listen_address, listen_port, connection_addresses


class Controller:
    def __init__(self, core, host, port):
        self.core, self.host, self.port = core, host, port
        self.server = None
        self.started = None
        self.error = ""
        self.lock = threading.RLock()

    def start(self, host=None, port=None):
        with self.lock:
            if self.server:
                return
            try:
                host = listen_address(host if host is not None else self.host)
                port = listen_port(port if port is not None else self.port)
            except ValueError as error:
                self.error = str(error)
                raise
            try:
                server = self.core.ChatServer((host, port), self.core.Handler)
            except OSError as error:
                if error.errno == errno.EADDRNOTAVAIL or getattr(error, 'winerror', None) == 10049:
                    self.error = '该监听地址不属于本机，请填写本机 IP，或选择 0.0.0.0 监听全部 IPv4 网卡。'
                else:
                    self.error = "端口已被占用或无法监听，请更换端口后重试。"
                raise ValueError(self.error) from error
            server.stopping = threading.Event()
            self.server, self.host, self.port = server, host, port
            self.started, self.error = time.time(), ""
            with self.core.connect() as db:
                for key, value in {'listen_host': host, 'listen_port': str(port)}.items():
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, value))
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

    def stop(self):
        with self.lock:
            if self.server:
                self.server.stopping.set()
                self.server.shutdown()
                self.server.server_close()
                self.server = None
                self.started = None

    def state(self):
        with self.core.connect() as db:
            self.core.relay.cleanup(db)
            accounts = [{**self.core.public_user(row), "disabled": bool(row["disabled"]), "created_at": row["created_at"]} for row in db.execute("SELECT * FROM users WHERE id!='system' ORDER BY created_at DESC")]
            devices = [dict(row) for row in db.execute("SELECT d.id,d.user_id,d.name,d.created_at,d.last_seen,d.revoked,u.nickname,u.username FROM devices d JOIN users u ON u.id=d.user_id ORDER BY d.last_seen DESC")]
            total, size = db.execute("SELECT COUNT(*),COALESCE(SUM(length(envelope)),0) FROM relay_packets").fetchone()
            settings = {"registration": self.core.relay.setting(db, "registration")}
            privacy = self.core.relay.privacy(db)
        with self.core.EVENTS.lock:
            sessions = [{"user_id": uid, "device_id": did} for uid, _, did in self.core.EVENTS.clients.values()]
        online = {s["device_id"] for s in sessions}
        for device in devices:
            device["online"] = device["id"] in online
        return {"running": bool(self.server), "host": self.host, "port": self.port, "uptime": int(time.time() - self.started) if self.started else 0,
                "error": self.error or getattr(self.server, "last_error", ""), "accounts": accounts, "devices": devices, "sessions": sessions,
                "online": len({s["user_id"] for s in sessions}), "queued": total, "queue_bytes": size, "settings": settings, "privacy": privacy,
                "addresses": connection_addresses(self.host, self.port), "data_dir": str(self.core.DATA)}

    def action(self, payload):
        action = payload.get("action")
        if action == "start":
            self.start(payload.get("host"), payload.get("port"))
        elif action == "stop":
            self.stop()
        elif action == "settings":
            with self.core.connect() as db:
                db.execute("UPDATE settings SET value=? WHERE key='registration'", ('1' if payload.get('registration') else '0',))
        elif action in ("disable", "enable", "disconnect", "revoke", "reset_avatar"):
            uid = payload.get("user_id")
            with self.core.connect() as db:
                if not db.execute("SELECT 1 FROM users WHERE id=? AND id!='system'", (uid,)).fetchone():
                    raise ValueError("账号不存在。")
                if action in ("disable", "enable"):
                    db.execute("UPDATE users SET disabled=? WHERE id=?", (int(action == "disable"), uid))
                if action == 'reset_avatar':
                    db.execute("UPDATE users SET avatar='flower' WHERE id=?", (uid,))
                if action in ("disable", "disconnect"):
                    db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
                if action == "revoke":
                    did = payload.get("device_id")
                    if not db.execute("SELECT 1 FROM devices WHERE id=? AND user_id=?", (did, uid)).fetchone():
                        raise ValueError("设备不存在。")
                    db.execute("UPDATE devices SET revoked=1 WHERE id=? AND user_id=?", (did, uid))
                    db.execute("DELETE FROM deliveries WHERE device_id=?", (did,))
                    self.core.relay.cleanup(db)
            if action in ("disable", "disconnect"):
                self.core.EVENTS.publish({"type": "expired"}, {uid})
            elif action == "revoke":
                self.core.EVENTS.publish({"type": "devices"})
            elif action == 'reset_avatar':
                self.core.EVENTS.publish({'type': 'profile'})
        else:
            raise ValueError("操作不存在。")


class AdminHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def respond(self, data, status=200, mime="application/json; charset=utf-8", cookie=None):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            return False
        try:
            cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie["weiyan_admin"].value if "weiyan_admin" in cookie else ""
            return hmac.compare_digest(token, self.server.token)
        except http.cookies.CookieError:
            return False

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/" and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}":
            token = parse_qs(parsed.query).get("token", [""])[0]
            if hmac.compare_digest(token, self.server.token):
                self.send_response(303)
                self.send_header("Set-Cookie", f"weiyan_admin={self.server.token}; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Location", "/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        if not self.authorized():
            return self.respond({"error": "请从服务端桌面窗口进入管理界面。"}, 403)
        if parsed.path == "/manage/state":
            return self.respond(self.server.controller.state())
        if parsed.path == "/manage/update/info":
            return self.respond(self.server.updater.info())
        if parsed.path == "/manage/update/status":
            return self.respond(self.server.updater.progress)
        target = self.server.public / {"/": "index.html", "/admin.js": "admin.js", "/motion.js": "motion.js", "/admin.css": "admin.css", "/logo.svg": "logo.svg"}.get(parsed.path, "missing")
        if not target.is_file():
            return self.respond({"error": "地址不存在。"}, 404)
        mime = "text/html" if target.suffix == ".html" else "text/css" if target.suffix == ".css" else "image/svg+xml" if target.suffix == ".svg" else "application/javascript"
        return self.respond(target.read_bytes(), mime=mime + "; charset=utf-8")

    def do_POST(self):
        host = f"127.0.0.1:{self.server.server_port}"
        if not self.authorized() or self.headers.get("X-Weiyan-Admin") != "desktop" or self.headers.get("Origin") not in (None, "http://" + host):
            return self.respond({"error": "管理请求来源无效。"}, 403)
        try:
            if self.path in ('/manage/update/check', '/manage/update/install'):
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 4096:
                    raise ValueError('请求长度无效。')
                self.rfile.read(length)
                value = self.server.updater.check() if self.path.endswith('/check') else self.server.updater.install()
                return self.respond(value)
            if self.path != "/manage/action" or self.headers.get_content_type() != "application/json":
                raise ValueError("管理请求格式不正确。")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("请求长度无效。")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效。")
            self.server.controller.action(payload)
            return self.respond({"ok": True})
        except (ValueError, TypeError, KeyError) as error:
            return self.respond({"error": str(error)}, 400)


def launch(core, args):
    controller = Controller(core, args.host, args.port)
    try:
        controller.start()
    except ValueError:
        pass
    admin = ThreadingHTTPServer(("127.0.0.1", args.admin_port), AdminHandler)
    admin.daemon_threads = True
    admin.token = secrets.token_urlsafe(32)
    admin.controller = controller
    admin.public = Path(__file__).resolve().parent / "admin_ui"
    admin.updater = Updater('server', core.DATA, controller.stop)
    url = f"http://127.0.0.1:{admin.server_port}/?token={admin.token}"
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    try:
        if args.browser:
            print("Admin: " + url, flush=True)
            if not args.no_open:
                webbrowser.open(url)
            while True:
                time.sleep(0.5)
        else:
            import webview
            window = webview.create_window("未言诗集 · 服务端管理", url, width=1250, height=850, min_size=(1020, 700), background_color="#fff8fa", text_select=True)
            from window_effects import WindowEffects
            effects = WindowEffects(window, close_window=True)
            import sys
            webview.start(gui="cocoa" if sys.platform == 'darwin' else "edgechromium", private_mode=True, icon=str(admin.public / "logo.ico"))
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        admin.shutdown()
        admin.server_close()
    return 0
