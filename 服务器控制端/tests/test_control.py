import http.cookiejar
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
