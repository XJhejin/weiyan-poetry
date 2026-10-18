"""Runs inside the actual APK: native libsodium, Keystore, HTTP and persistence."""
import base64
import http.cookiejar
import json
from pathlib import Path
import secrets
import urllib.request
from secure import SecureClient, protect, canonical


class Remote:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, path, data=None):
        request = urllib.request.Request(server_url + path, data=canonical(data) if data is not None else None,
            headers={'Content-Type': 'application/json', 'Origin': server_url, 'X-Weiyan-Client': 'web'})
        with self.http.open(request, timeout=20) as response:
            return json.loads(response.read())

    def secure(self, name):
        self.call('/api/register', {'username': name + secrets.token_hex(4), 'password': 'test-only-Android-4402', 'nickname': name})
        cookie = '; '.join(c.name + '=' + c.value for c in self.jar)
        return SecureClient(Path(test_profile) / name, server_url, cookie, self.call('/api/bootstrap'))


sealed = protect(b'android-key-custody-test')
assert b'android-key-custody-test' not in sealed
assert protect(sealed, True) == b'android-key-custody-test'
try:
    protect(sealed[:-1] + bytes([sealed[-1] ^ 1]), True)
    raise AssertionError('Keystore accepted tampered ciphertext')
except AssertionError:
    raise
except Exception:
    pass

alice, bob = Remote(), Remote()
a, b = alice.secure('android_a'), bob.secure('android_b')
cid = alice.call('/api/conversations', {'peer_id': b.me['id']})['conversation']['id']
secret = 'Android-Windows-compatible-encryption-' + secrets.token_hex(8)
a.send({'conversation_id': cid, 'body': secret, 'client_id': 'android-' + secrets.token_hex(8)})
assert any(m['body'] == secret for m in b.receive())
file = b'android-encrypted-file-content-' + secrets.token_bytes(64)
a.vault.put('file', 'testfile', {'data': base64.b64encode(file).decode(), 'name': 'phone.txt', 'size': len(file), 'kind': 'file', 'mime': 'application/octet-stream'})
sent = a.send({'conversation_id': cid, 'body': '', 'file_id': 'testfile', 'client_id': 'file-' + secrets.token_hex(8)})
received = next(m for m in b.receive() if m['id'] == sent['id'])
stored = b.vault.get('file', received['attachment']['url'].rsplit('/', 1)[-1])
assert base64.b64decode(stored['data']) == file
assert file not in b.vault.db_path.read_bytes()
again = SecureClient(Path(test_profile) / 'android_b', server_url, b.cookie, bob.call('/api/bootstrap'))
assert any(m['body'] == secret for m in again.vault.messages(cid))
smoke_result = 'PASS'
