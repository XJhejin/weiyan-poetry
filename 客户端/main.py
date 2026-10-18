"""Native Windows desktop client, with an isolated local UI gateway."""
from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs
import webbrowser
import hashlib
import base64
import re
import time
from secure import SecureClient, SecurityError, RemoteError, protect
from update import Updater
from release_config import VERSION

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "ui"
PROFILE = Path(os.environ.get("WEIYAN_CLIENT_DATA", str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WeiyanPoetry" / "client")))
CONFIG_FILE = PROFILE / "config.json"
CONFIG_LOCK = threading.Lock()
CONFIG = {"server_url": "http://127.0.0.1:8765"}


def load_config():
    PROFILE.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def save_server(value):
    if not isinstance(value, str):
        raise ValueError("请输入服务端地址。")
    value = value.strip().rstrip("/")
    if "://" not in value:
        value = "http://" + value
    parsed = urlsplit(value)
    try:
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment and (parsed.port is None or 1 <= parsed.port <= 65535)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("地址示例：http://192.168.1.8:8765")
    with CONFIG_LOCK:
        CONFIG["server_url"] = value
        temporary = CONFIG_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(CONFIG_FILE)
    return value


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def json_response(self, data, status=200, clear_cookie=False):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if clear_cookie:
            self.send_header("Set-Cookie", "weiyan_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
        self.end_headers()
        self.wfile.write(raw)

    def valid_local_request(self):
        host = self.headers.get("Host", "")
        if host != f"127.0.0.1:{self.server.server_port}":
            return False
        if self.command == "POST":
            origin = self.headers.get("Origin")
            return self.headers.get("X-Weiyan-Client") == "web" and (not origin or origin == f"http://{host}")
        return True

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        try:
            if not self.valid_local_request():
                return self.json_response({"error": "请求来源不正确。"}, 403)
            path = urlsplit(self.path).path
            if path.startswith('/desktop/update/'):
                operation = path.rsplit('/', 1)[-1]
                if self.command == 'POST':
                    self.read_json()
                if operation == 'info' and self.command == 'GET':
                    return self.json_response(self.server.updater.info())
                if operation == 'status' and self.command == 'GET':
                    return self.json_response(self.server.updater.progress)
                if operation == 'check' and self.command == 'POST':
                    return self.json_response(self.server.updater.check())
                if operation == 'install' and self.command == 'POST':
                    return self.json_response(self.server.updater.install())
                return self.json_response({'error': '请求无效。'}, 400)
            if path == "/desktop/config":
                if self.command == "GET":
                    return self.json_response({"server_url": CONFIG["server_url"], "native": not getattr(self.server, "browser_mode", False), "version": VERSION})
                length = int(self.headers.get("Content-Length", "0"))
                if length > 2048 or length < 0:
                    return self.json_response({"error": "地址太长了。"}, 400)
                data = json.loads(self.rfile.read(length))
                value = save_server(data.get("server_url"))
                return self.json_response({"server_url": value}, clear_cookie=True)
            if path == "/api/socket":
                origin = self.headers.get("Origin")
                if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                    return self.json_response({"error": "请求来源不正确。"}, 403)
                return self.websocket_proxy()
            if path == "/api/bootstrap" or path.startswith(("/desktop/security", "/desktop/storage", "/local-images/")) or path in ("/api/messages", "/api/upload", "/api/search", "/api/read") or re.fullmatch(r"/api/conversations/[^/]+/messages", path):
                return self.secure_api(path)
            if path.startswith(("/api/", "/uploads/")):
                return self.proxy()
            if self.command != "GET":
                return self.json_response({"error": "没有找到这个地址。"}, 404)
            path = "/index.html" if path == "/" else unquote(path)
            target = (PUBLIC / path.lstrip("/")).resolve()
            if not target.is_relative_to(PUBLIC.resolve()) or not target.is_file():
                return self.json_response({"error": "文件不存在。"}, 404)
            raw = target.read_bytes()
            mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") or mime in ("application/javascript", "image/svg+xml") else ""))
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-eval'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; "
                             "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            self.end_headers()
            self.wfile.write(raw)
        except RemoteError as exc:
            self.json_response({"error": str(exc), "offline": exc.status == 503}, exc.status)
        except (ValueError, AttributeError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc) if isinstance(exc, ValueError) else "设置格式不正确。"}, 400)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass

    def context(self):
        cookie = self.headers.get("Cookie", "")
        server_url = CONFIG["server_url"]
        key = hashlib.sha256((server_url + cookie).encode()).hexdigest()
        cache = PROFILE / 'sessions' / (key + '.dpapi')
        try:
            status, _, raw = self.upstream("/api/bootstrap")
            data = json.loads(raw)
        except RemoteError as error:
            if error.status != 503 or not cache.is_file():
                raise
            data = json.loads(protect(cache.read_bytes(), True))
            data['offline'] = True
            status = 200
        if status != 200:
            raise RemoteError(data.get("error", "请先登录。"), status)
        if not data.get("server_id"):
            raise RemoteError("请先更新服务端到加密版本。", 409)
        if not data.get('offline'):
            with self.server.context_lock:
                cache.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache.with_suffix('.tmp')
                temporary.write_bytes(protect(json.dumps(data, ensure_ascii=False).encode()))
                temporary.replace(cache)
        with self.server.context_lock:
            context = self.server.contexts.get(key)
            if context is None:
                context = SecureClient(PROFILE, server_url, cookie, data)
                self.server.contexts[key] = context
            else:
                context.me = data["me"]
        return context, data

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 8 * 1024 * 1024:
            raise ValueError("请求太大。")
        result = json.loads(self.rfile.read(length))
        if not isinstance(result, dict):
            raise ValueError("请求格式不正确。")
        return result

    def secure_api(self, path):
        context, bootstrap = self.context()
        params = parse_qs(urlsplit(self.path).query)
        payload = self.read_json() if self.command == "POST" else {}
        if path == "/api/bootstrap":
            return self.json_response(context.bootstrap(bootstrap))
        if path == "/desktop/security":
            cid = payload.get("conversation_id") if payload else params.get("conversation_id", [None])[0]
            return self.json_response(context.security(cid, payload.get("approve", False), payload.get("directory_digest")))
        if path == "/desktop/storage":
            if self.command == "POST":
                values = payload.get("values", {})
                if not isinstance(values, dict) or any(k not in {"outbox", "drafts", "saved", "pins", "muted", "notifications", "lastConversation"} for k in values):
                    raise ValueError("本机设置格式不正确。")
                for key, value in values.items():
                    context.vault.put("ui", key, value)
            return self.json_response({"ok": True})
        if path == "/api/search":
            return self.json_response({"messages": context.vault.messages(search=params.get("q", [""])[0][:100], limit=50)})
        if path == "/api/upload":
            raw = base64.b64decode(payload.get("data", ""), validate=True)
            if len(raw) > 5 * 1024 * 1024:
                raise ValueError("请选择 5 MB 以内的图片。")
            if raw.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif raw.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif raw.startswith((b"GIF87a", b"GIF89a")):
                mime = "image/gif"
            elif len(raw) > 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                raise ValueError("支持 PNG、JPG、GIF 和 WebP 图片。")
            image_id = secrets.token_hex(20)
            context.vault.put("image", image_id, {"data": base64.b64encode(raw).decode(), "mime": mime})
            return self.json_response({"id": image_id, "url": "/local-images/" + image_id})
        if path.startswith("/local-images/"):
            image = context.vault.get("image", path.rsplit("/", 1)[-1])
            if not image:
                return self.json_response({"error": "图片不存在。"}, 404)
            raw = base64.b64decode(image["data"], validate=True)
            self.send_response(200)
            self.send_header("Content-Type", image["mime"])
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/messages":
            return self.json_response({"message": context.send(payload)}, 201)
        if path == "/api/read":
            cid, last = payload.get("conversation_id"), payload.get("last_id")
            if not isinstance(cid, str) or not isinstance(last, int):
                raise ValueError("已读信息格式不正确。")
            if cid not in {c["id"] for c in bootstrap["conversations"]}:
                raise RemoteError("无权查看此会话。", 403)
            maximum = context.vault.messages(cid, limit=1)
            last = min(last, maximum[0]["id"] if maximum else 0)
            context.vault.put("read", cid, max(context.vault.get("read", cid, 0), last))
            context.remote("/api/read", {"conversation_id": cid, "last_id": last})
            return self.json_response({"ok": True})
        match = re.fullmatch(r"/api/conversations/([^/]+)/messages", unquote(path))
        if match:
            cid = match[1]
            if cid not in {c["id"] for c in bootstrap["conversations"]}:
                raise RemoteError("你暂时无法查看这个会话。", 403)
            before = int(params.get("before", ["9223372036854775807"])[0])
            if before < 0 or before > 9223372036854775807:
                raise ValueError("消息编号无效。")
            try:
                context.receive()
            except RemoteError as error:
                if error.status != 503:
                    raise
            messages = context.vault.messages(cid, before)
            return self.json_response({"messages": list(reversed(messages[:50])), "has_more": len(messages) > 50})
        return self.json_response({"error": "没有找到这个地址。"}, 404)

    def upstream(self, path, data=None):
        parsed = urlsplit(CONFIG["server_url"])
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = cls(parsed.hostname, parsed.port, timeout=20)
        headers = {"Host": parsed.netloc, "Origin": CONFIG["server_url"], "X-Weiyan-Client": "web", "Cookie": self.headers.get("Cookie", ""), "Content-Type": "application/json"}
        try:
            connection.request("POST" if data is not None else "GET", path, body=json.dumps(data, ensure_ascii=False).encode() if data is not None else None, headers=headers)
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        except (OSError, http.client.HTTPException) as error:
            raise RemoteError("还没有连接到服务端。请先启动服务端，或检查连接地址。", 503) from error
        finally:
            connection.close()

    def websocket_proxy(self):
        from simple_websocket import Server, Client, ConnectionClosed, ConnectionError
        context, _ = self.context()
        class PortAwareClient(Client):
            def handshake(self):
                hostname = self.host
                self.host = f"[{hostname}]:{self.port}" if ":" in hostname else f"{hostname}:{self.port}"
                try:
                    super().handshake()
                finally:
                    self.host = hostname
        parsed = urlsplit(CONFIG["server_url"])
        try:
            upstream = PortAwareClient.connect(("wss" if parsed.scheme == "https" else "ws") + "://" + parsed.netloc + "/api/socket?device_id=" + context.device_id,
                                      headers={"Cookie": self.headers.get("Cookie", ""), "Origin": CONFIG["server_url"]},
                                      ping_interval=12, max_message_size=65536)
        except (ConnectionError, OSError):
            return self.json_response({"error": "暂时无法连接到服务端。"}, 503)
        environ = {"werkzeug.socket": self.connection, "REQUEST_METHOD": "GET", "SERVER_PROTOCOL": "HTTP/1.1"}
        environ.update({"HTTP_" + name.upper().replace("-", "_"): value for name, value in self.headers.items()})
        local = None
        self.close_connection = True
        polled = 0
        try:
            local = Server(environ, ping_interval=12, max_message_size=32768)
            while local.connected and upstream.connected:
                packet = local.receive(timeout=0.03)
                if packet is not None:
                    payload = {}
                    try:
                        payload = json.loads(packet)
                        if not isinstance(payload, dict):
                            raise ValueError("消息格式不正确。")
                        if payload.get("op") == "ping":
                            upstream.send(json.dumps({"op": "ping"}))
                        elif payload.get("op") == "message":
                            message = context.send(payload)
                            local.send(json.dumps({"type": "ack", "client_id": payload["client_id"], "message": message}, ensure_ascii=False))
                        else:
                            raise ValueError("不支持的消息类型。")
                    except ValueError as error:
                        local.send(json.dumps({"type": "nack", "client_id": payload.get("client_id") if isinstance(payload, dict) else None, "error": str(error)}, ensure_ascii=False))
                packet = upstream.receive(timeout=0.03)
                if packet is not None:
                    event = json.loads(packet)
                    if event.get("type") == "relay":
                        polled = 0
                    else:
                        local.send(packet)
                if time.monotonic() - polled > 1.5:
                    for message in context.receive():
                        local.send(json.dumps({"type": "message", "message": message}, ensure_ascii=False))
                    if context.blocked:
                        local.send(json.dumps({"type": "security", "blocked": context.blocked}, ensure_ascii=False))
                    polled = time.monotonic()
        except (ConnectionClosed, OSError, RemoteError):
            pass
        finally:
            for connection in (local, upstream):
                if connection:
                    try:
                        connection.close()
                    except (ConnectionClosed, OSError):
                        pass

    def proxy(self):
        parsed = urlsplit(CONFIG["server_url"])
        streaming = urlsplit(self.path).path == "/api/events"
        connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_cls(parsed.hostname, parsed.port, timeout=20 if streaming else 8)
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 8 * 1024 * 1024:
            self.close_connection = True
            return self.json_response({"error": "图片太大了，请选择 5 MB 以内的图片。"}, 413)
        body = self.rfile.read(length) if self.command == "POST" else None
        headers = {"Host": parsed.netloc, "Origin": f"{parsed.scheme}://{parsed.netloc}", "X-Weiyan-Client": "web"}
        for name in ("Cookie", "Content-Type", "Last-Event-ID"):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException):
            connection.close()
            return self.json_response({"error": "还没有连接到服务端。请先启动服务端，或检查连接地址。", "offline": True}, 503)
        try:
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ("connection", "transfer-encoding", "server", "date", "content-length"):
                    self.send_header(name, value)
            if streaming and response.status == 200:
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    line = response.readline()
                    if not line:
                        break
                    self.wfile.write(line)
                    self.wfile.flush()
                self.close_connection = True
            else:
                raw = response.read()
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        except (OSError, http.client.HTTPException):
            self.close_connection = True
        finally:
            connection.close()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true", help="使用浏览器预览客户端")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--server", help="设置服务端地址")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    load_config()
    if args.server:
        save_server(args.server)
    gateway = LocalServer(("127.0.0.1", args.port), Gateway)
    gateway.browser_mode = args.browser
    gateway.contexts = {}
    gateway.context_lock = threading.RLock()
    gateway.updater = Updater('client', PROFILE)
    url = f"http://127.0.0.1:{gateway.server_port}"
    if args.browser:
        print(f"Client: {url}", flush=True)
        if not args.no_open:
            webbrowser.open(url)
        try:
            gateway.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            gateway.server_close()
        return
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    import webview
    icon = str(PUBLIC / "assets" / "logo.ico")
    window = webview.create_window("未言诗集", url, width=1360, height=880, min_size=(900, 650),
                                  background_color="#fff8fa", text_select=True)
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(PROFILE / "webview"), icon=icon)
    finally:
        gateway.shutdown()
        gateway.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        PROFILE.mkdir(parents=True, exist_ok=True)
        import traceback
        (PROFILE / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        if getattr(sys, "frozen", False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"启动遇到问题：{error}\n\n详细记录保存在：{PROFILE / 'error.log'}", "未言诗集", 16)
        else:
            raise
