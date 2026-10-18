"""Android entrypoint: the same secure client and gateway as the Windows app."""
import os
import secrets
import threading
import http.cookies
import hmac
from urllib.parse import urlsplit, parse_qs
from pathlib import Path

_gateway = None
_url = None


def start(profile, ui):
    global _gateway, _url
    if _gateway:
        return _url
    os.environ['WEIYAN_PLATFORM'] = 'android'
    os.environ['WEIYAN_CLIENT_DATA'] = profile
    import main
    from update import Updater
    main.PROFILE = Path(profile)
    main.CONFIG_FILE = main.PROFILE / 'config.json'
    main.PUBLIC = Path(ui)
    main.load_config()
    token = secrets.token_urlsafe(32)

    class AndroidGateway(main.Gateway):
        def dispatch(self):
            query = parse_qs(urlsplit(self.path).query)
            if self.command == 'GET' and urlsplit(self.path).path == '/' and hmac.compare_digest(query.get('local_key', [''])[0], token):
                self.send_response(303)
                self.send_header('Set-Cookie', f'weiyan_local={token}; Path=/; HttpOnly; SameSite=Strict')
                self.send_header('Location', '/')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            return super().dispatch()

        def valid_local_request(self):
            if not super().valid_local_request():
                return False
            try:
                cookies = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
                return 'weiyan_local' in cookies and hmac.compare_digest(cookies['weiyan_local'].value, token)
            except http.cookies.CookieError:
                return False

    _gateway = main.LocalServer(('127.0.0.1', 0), AndroidGateway)
    _gateway.browser_mode = False
    _gateway.contexts = {}
    _gateway.context_lock = threading.RLock()
    _gateway.updater = Updater('client', main.PROFILE)
    from background import BackgroundAgent
    def notify(message):
        if main.CONFIG.get('native_notifications', True):
            from java import jclass
            jclass('com.weiyan.poetry.NotificationService').notifyMessage(message['conversation_id'])
    _gateway.background = BackgroundAgent(_gateway, main.PROFILE, lambda: main.CONFIG['server_url'], notify)
    _gateway.background.start()
    threading.Thread(target=_gateway.serve_forever, daemon=True).start()
    _url = f'http://127.0.0.1:{_gateway.server_port}/?local_key={token}'
    return _url


def install_apk(path):
    from java import jclass
    jclass('com.weiyan.poetry.MainActivity').installUpdate(str(path))
