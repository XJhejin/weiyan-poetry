import http.cookiejar
import base64
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from connection import Remote
from credentials import protect


class LocalUITests(unittest.TestCase):
    def test_authentication_host_and_origin_are_required_before_rpc(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), main.Handler)
        server.token = 'test-control-token'
        server.remote = Mock()
        server.remote.call.return_value = {'running': True, 'accounts': []}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def request(path, payload=None, **headers):
            h = {'Content-Type': 'application/json', 'X-Weiyan-Admin': 'desktop', 'Origin': base, **headers}
            req = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=h)
            try:
                return opener.open(req)
            except urllib.error.HTTPError as error:
                return error
        try:
            with request('/manage/state') as response:
                self.assertEqual(response.status, 403)
            server.remote.call.assert_not_called()
            with request('/?token=test-control-token') as response:
                self.assertIn('服务器控制端'.encode(), response.read())
            with request('/manage/state') as response:
                self.assertEqual(response.status, 200)
            server.remote.call.assert_called_once_with('state')
            server.remote.call.reset_mock()
            for headers in ({'Origin': 'https://untrusted.example'}, {'Host': 'untrusted.example'}, {'X-Weiyan-Admin': 'wrong'}):
                with request('/manage/restart', {}, **headers) as response:
                    self.assertEqual(response.status, 403)
            server.remote.call.assert_not_called()
            with request('/manage/restart', {}) as response:
                self.assertEqual(response.status, 200)
            server.remote.call.assert_called_once_with('restart', {})
        finally:
            server.shutdown()
            server.server_close()


class ConnectionTests(unittest.TestCase):
    def test_manual_settings_validate_host_port_and_pin(self):
        settings = {'host': '156.225.28.175', 'port': 22, 'user': 'root'}
        raw = b'\0\0\0\x0bssh-ed25519' + b'\0\0\0\x20' + bytes(range(32))
        line = settings['host'] + ' ssh-ed25519 ' + base64.b64encode(raw).decode()
        self.assertTrue(Remote.key_line(settings, line)[1].startswith('SHA256:'))
        for change in ({'host': '-oProxyCommand=bad'}, {'host': 'https://example.com:22'}, {'port': True}, {'port': 0}, {'user': 'root@elsewhere'}):
            with self.assertRaises(ValueError): Remote.validate({**settings, **change})
        with self.assertRaises(ValueError): Remote.key_line({**settings, 'host': 'elsewhere'}, line)

    def test_manual_connection_failure_keeps_saved_authorization(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)
            settings = {'host': '156.225.28.175', 'port': 22, 'user': 'root'}
            raw = b'\0\0\0\x0bssh-ed25519' + b'\0\0\0\x20' + bytes(range(32))
            line = settings['host'] + ' ssh-ed25519 ' + base64.b64encode(raw).decode()
            config = json.dumps({**settings, 'host_key': line})
            (profile / 'connection.json').write_text(config, 'utf-8')
            (profile / 'identity.dpapi').write_bytes(b'encrypted-existing-key')
            remote = Remote(profile)
            def make_private(path): Path(path).mkdir(parents=True, exist_ok=True)
            with patch('connection.private_directory', side_effect=make_private), patch('connection.private_file'), patch.object(Remote, '_call', side_effect=ValueError('connection failed')):
                with self.assertRaisesRegex(ValueError, 'connection failed'):
                    remote.configure({**settings, 'host_key': line})
            self.assertEqual((profile / 'connection.json').read_text('utf-8'), config)
            self.assertEqual((profile / 'identity.dpapi').read_bytes(), b'encrypted-existing-key')
            with patch('connection.private_directory', side_effect=make_private), patch('connection.private_file'), patch.object(Remote, '_call', return_value={'running': True}):
                result = remote.configure({**settings, 'host_key': line})
            self.assertTrue(result['ok'])
            saved = json.loads((profile / 'connection.json').read_text('utf-8'))
            self.assertEqual(remote.key_file(saved).read_bytes(), b'encrypted-existing-key')
            self.assertNotIn('private_key', saved)

    def test_new_server_requires_explicit_fingerprint_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            remote = Remote(folder)
            raw = b'\0\0\0\x0bssh-ed25519' + b'\0\0\0\x20' + bytes(range(32))
            with self.assertRaisesRegex(ValueError, '核对主机指纹'):
                remote.configure({'host': 'example.com', 'port': 22, 'user': 'root', 'host_key': 'example.com ssh-ed25519 ' + base64.b64encode(raw).decode()})

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_private_key_is_account_encrypted(self):
        raw = b'not-a-real-private-key'
        encrypted = protect(raw)
        self.assertNotIn(raw, encrypted)
        self.assertEqual(protect(encrypted, decrypt=True), raw)

    def test_pinned_ssh_identity_is_mandatory_and_credentials_are_not_in_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            remote = Remote(directory)
            identity = Path(directory)
            settings = {'host': '156.225.28.175', 'port': 22, 'user': 'root'}
            response = Mock(returncode=0, stdout=b'{"ok":true,"data":{"running":true}}', stderr=b'')
            with patch.object(remote, 'identity', return_value=(settings, identity)), \
                    patch('connection.subprocess.run', return_value=response) as process, \
                    patch('connection.Path.is_file', return_value=True):
                self.assertTrue(remote.call('state')['running'])
                args = process.call_args.args[0]
                self.assertIn('StrictHostKeyChecking=yes', args)
                self.assertIn('BatchMode=yes', args)
                self.assertIn('IdentitiesOnly=yes', args)
                self.assertNotIn('shell', process.call_args.kwargs)
                self.assertEqual(json.loads(process.call_args.kwargs['input'])['operation'], 'state')
                response.returncode, response.stderr = 255, b'Host key verification failed'
                with self.assertRaisesRegex(ValueError, '身份校验失败'):
                    remote.call('state')


if __name__ == '__main__':
    unittest.main(verbosity=2)
