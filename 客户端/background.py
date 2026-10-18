"""Native background reception; persisted session is protected by the OS key."""
import hashlib
import json
from pathlib import Path
import threading
import time
from secure import SecureClient, protect, RemoteError


class BackgroundAgent:
    def __init__(self, gateway, profile, server_url, notify):
        self.gateway, self.profile, self.server_url, self.notify = gateway, Path(profile), server_url, notify
        self.active = None
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.saved = self.profile / 'background-session.dpapi'

    def start(self):
        try:
            saved = json.loads(protect(self.saved.read_bytes(), True))
            if saved['server_url'] == self.server_url():
                bootstrap = {**saved['bootstrap'], 'offline': True}
                context = SecureClient(self.profile, saved['server_url'], saved['cookie'], bootstrap)
                key = hashlib.sha256((saved['server_url'] + saved['cookie']).encode()).hexdigest()
                self.gateway.contexts[key] = context
                self.activate(context, bootstrap, persist=False)
        except (OSError, ValueError, KeyError):
            pass
        threading.Thread(target=self.run, daemon=True, name='encrypted-background-receiver').start()

    def activate(self, context, bootstrap, persist=True):
        with self.lock:
            context.on_received = lambda message: self.received(context, message)
            self.active = context
            if persist:
                raw = json.dumps({'server_url': context.url, 'cookie': context.cookie, 'bootstrap': bootstrap}, ensure_ascii=False).encode()
                temporary = self.saved.with_suffix('.tmp')
                temporary.write_bytes(protect(raw))
                temporary.replace(self.saved)

    def received(self, context, message):
        with self.lock:
            if self.active is not context or message['sender']['id'] == context.me['id']:
                return
        if message['conversation_id'] in context.vault.get('ui', 'muted', []):
            return
        try:
            self.notify(message)
        except Exception:
            pass  # A system notification must not prevent ciphertext acknowledgement.

    def clear(self):
        with self.lock:
            self.active = None
            self.saved.unlink(missing_ok=True)

    def run(self):
        while not self.stop_event.wait(2):
            with self.lock:
                context = self.active
            if context is None or getattr(context, 'active_sockets', 0):
                continue
            try:
                context.receive()
            except (RemoteError, ValueError, OSError):
                pass

    def stop(self):
        self.stop_event.set()
