"""Independent Windows GUI for the deployed Linux server."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.cookies
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs
import webbrowser

from connection import Remote
from release_config import VERSION

PUBLIC = Path(__file__).resolve().parent / 'ui'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send(self, value, status=200, mime='application/json; charset=utf-8'):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            return False
        try:
            cookie = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
            return hmac.compare_digest(cookie['weiyan_control'].value if 'weiyan_control' in cookie else '', self.server.token)
        except http.cookies.CookieError:
            return False

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == '/' and self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}':
            token = parse_qs(parsed.query).get('token', [''])[0]
            if hmac.compare_digest(token, self.server.token):
                self.send_response(303)
                self.send_header('Set-Cookie', f'weiyan_control={self.server.token}; Path=/; HttpOnly; SameSite=Strict')
                self.send_header('Location', '/')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        if not self.authorized():
            return self.send({'error': '请从服务器控制端窗口进入。'}, 403)
        operations = {'/manage/state': 'state', '/manage/host': 'host', '/manage/logs': 'logs',
                      '/manage/update/info': 'update_info', '/manage/update/status': 'update_status'}
        try:
            if parsed.path == '/manage/connection':
                return self.send(self.server.remote.info())
            if parsed.path == '/manage/network':
                return self.send(self.server.remote.network())
            if parsed.path in operations:
                return self.send(self.server.remote.call(operations[parsed.path]))
            names = {'/': 'index.html', '/admin.js': 'admin.js', '/connection.js': 'connection.js', '/motion.js': 'motion.js',
                     '/admin.css': 'admin.css', '/logo.svg': 'logo.svg'}
            if parsed.path not in names:
                return self.send({'error': '地址不存在。'}, 404)
            target = PUBLIC / names[parsed.path]
            mime = {'.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'}[target.suffix]
            return self.send(target.read_bytes(), mime=mime + '; charset=utf-8')
        except (ValueError, OSError) as error:
            return self.send({'error': str(error)}, 502)

    def do_POST(self):
        origin = f'http://127.0.0.1:{self.server.server_port}'
        if not self.authorized() or self.headers.get('X-Weiyan-Admin') != 'desktop' or self.headers.get('Origin') not in (None, origin):
            return self.send({'error': '管理请求来源无效。'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get_content_type() != 'application/json' or not 0 < length <= 49152:
                raise ValueError('管理请求格式无效。')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('管理请求格式无效。')
            if self.path == '/manage/connection/scan':
                return self.send(self.server.remote.scan(payload))
            if self.path == '/manage/connection/connect':
                return self.send(self.server.remote.configure(payload))
            operations = {'/manage/action': 'action', '/manage/restart': 'restart',
                          '/manage/update/check': 'update_check', '/manage/update/install': 'update_install'}
            if self.path not in operations:
                raise ValueError('操作不存在。')
            return self.send(self.server.remote.call(operations[self.path], payload))
        except (ValueError, OSError) as error:
            return self.send({'error': str(error)}, 400)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--access-file', type=Path)
    parser.add_argument('--self-test', type=Path)
    args = parser.parse_args()
    remote = Remote()
    if args.self_test:
        try:
            host, state = remote.call('host'), remote.call('state')
            report = {'ok': True, 'control_version': VERSION, 'server_version': host['version'],
                      'running': state['running'], 'accounts': len(state['accounts']),
                      'private_fields_excluded': all('password' not in a for a in state['accounts'])}
            args.self_test.write_text(json.dumps(report), encoding='utf-8')
            return 0
        except Exception as error:
            args.self_test.write_text(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), encoding='utf-8')
            return 1
        finally:
            remote.close()
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    http.daemon_threads = True
    http.token = secrets.token_urlsafe(32)
    http.remote = remote
    url = f'http://127.0.0.1:{http.server_port}/?token={http.token}'
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        if args.access_file:
            args.access_file.write_text(url, encoding='utf-8')
        if args.browser:
            if not args.no_open:
                webbrowser.open(url)
            while True:
                time.sleep(0.5)
        else:
            import webview
            window = webview.create_window('未言诗集 · 服务器控制端', url, width=1280, height=880,
                                           min_size=(1060, 720), background_color='#fff8fa', text_select=True)
            from window_effects import WindowEffects
            effects = WindowEffects(window, close_window=True)
            webview.start(gui='edgechromium', private_mode=True, icon=str(PUBLIC / 'logo.ico'))
    except KeyboardInterrupt:
        pass
    finally:
        http.shutdown()
        http.server_close()
        remote.close()
        if args.access_file:
            args.access_file.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        if getattr(sys, 'frozen', False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), '未言诗集服务器控制端', 16)
        else:
            raise
