"""Exercise actual SSH password exchanges without using production credentials."""
from contextlib import ExitStack
import base64
import io
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nacl.secret import SecretBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connection import Remote
from credentials import protect

GATEWAY = '/opt/weiyan/control/venv/bin/python /opt/weiyan/control/control_gateway.py'
TEST_PASSWORD = 'isolated-ssh-fixture-口令'
logging.getLogger('paramiko.transport').setLevel(logging.CRITICAL)


def host_key():
    raw = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    return paramiko.Ed25519Key.from_private_key(io.StringIO(raw.decode()))


class FixtureServer(paramiko.ServerInterface):
    def __init__(self, fixture):
        self.fixture = fixture
        self.command = threading.Event()

    def check_auth_password(self, username, password):
        self.fixture.attempts += 1
        return paramiko.AUTH_SUCCESSFUL if username == 'test-admin' and password == TEST_PASSWORD else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'password'

    def check_channel_request(self, kind, identifier):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *args):
        self.fixture.pty_requests += 1
        return False

    def check_channel_exec_request(self, channel, command):
        self.fixture.commands.append(command.decode())
        if command.decode() != GATEWAY:
            return False
        self.command.set()
        return True


class SSHFixture:
    def __init__(self):
        self.key = host_key()
        self.attempts = self.pty_requests = 0
        self.commands, self.requests, self.workers = [], [], []
        self.stopped = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0));self.listener.listen(8);self.listener.settimeout(.2)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True);self.thread.start()

    def settings(self):
        return {'host': '127.0.0.1', 'port': self.port, 'user': 'test-admin', 'auth_method': 'password',
                'host_key': f'[127.0.0.1]:{self.port} ssh-ed25519 {self.key.get_base64()}'}

    def serve(self):
        while not self.stopped.is_set():
            try: client, _ = self.listener.accept()
            except socket.timeout: continue
            except OSError: break
            worker = threading.Thread(target=self.handle, args=(client,), daemon=True)
            self.workers.append(worker);worker.start()

    def handle(self, client):
        transport = paramiko.Transport(client)
        try:
            transport.add_server_key(self.key)
            server = FixtureServer(self)
            transport.start_server(server=server)
            channel = transport.accept(10)
            if channel is None or not server.command.wait(5): return
            raw = channel.makefile('rb').readline(16385)
            request = json.loads(raw);self.requests.append(request)
            if request['operation'] == 'host':
                data = {'version': '2.2.2', 'hostname': 'isolated-test', 'system': 'Linux',
                        'address': 'https://156.225.28.175:23074', 'load': [0, 0, 0],
                        'memory_total': 2**30, 'memory_available': 2**29,
                        'disk_total': 10 * 2**30, 'disk_free': 9 * 2**30,
                        'proxy': {'ActiveState': 'active'}, 'update': {'stage': 'idle'}}
            elif request['operation'] == 'update_info':
                data = {'current': '2.2.2', 'repository': 'XJhejin/weiyan-poetry'}
            else:
                data = {'running': True, 'accounts': [], 'sessions': [], 'devices': [],
                        'online': 0, 'queued': 0, 'queue_bytes': 0, 'uptime': 60,
                        'host': '127.0.0.1', 'port': 23174, 'error': '',
                        'settings': {'registration': '1'}, 'data_dir': '/isolated-test'}
            channel.sendall(json.dumps({'ok': True, 'data': data}).encode())
            channel.send_exit_status(0);channel.shutdown_write()
            self.stopped.wait(.08)
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()

    def close(self):
        self.stopped.set();self.listener.close();self.thread.join(3)
        for worker in self.workers: worker.join(3)


class PasswordTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.profile = Path(self.directory.name)
        self.fixture = SSHFixture()
        self.stack = ExitStack()
        if os.name != 'nt':
            box = SecretBox(bytes(range(32)))
            self.stack.enter_context(patch('connection.protect', side_effect=lambda raw, decrypt=False:
                                          box.decrypt(raw) if decrypt else bytes(box.encrypt(raw))))
            self.stack.enter_context(patch('connection.private_directory', side_effect=lambda path:Path(path).mkdir(parents=True, exist_ok=True)))
            self.stack.enter_context(patch('connection.private_file'))
        self.remote = Remote(self.profile)

    def tearDown(self):
        self.remote.close();self.fixture.close();self.stack.close();self.directory.cleanup()

    def configure(self, **changes):
        return self.remote.configure({**self.fixture.settings(), 'password': TEST_PASSWORD, 'trust_host': True, **changes})

    def test_session_password_is_not_saved_and_reopening_requires_entry(self):
        self.assertTrue(self.configure()['ok'])
        self.assertTrue(self.remote.call('state')['running'])
        self.assertTrue(self.remote.info()['has_password'])
        self.assertFalse(self.remote.info()['password_saved'])
        self.assertEqual([p.name for p in self.profile.iterdir()], ['connection.json'])
        self.assertNotIn(TEST_PASSWORD.encode(), (self.profile / 'connection.json').read_bytes())
        with self.assertRaisesRegex(ValueError, '请输入|输入 SSH 密码'):
            Remote(self.profile).call('state')
        self.assertEqual(set(self.fixture.commands), {GATEWAY})
        self.assertEqual(self.fixture.pty_requests, 0)

    def test_saved_password_is_account_encrypted_and_can_be_reopened(self):
        self.configure(remember_password=True)
        settings = json.loads((self.profile / 'connection.json').read_text('utf-8'))
        encrypted = self.remote.password_file(settings).read_bytes()
        self.assertNotIn(TEST_PASSWORD.encode(), encrypted)
        self.assertTrue(self.remote.info()['password_saved'])
        other = Remote(self.profile)
        try: self.assertTrue(other.call('state')['running'])
        finally: other.close()
        self.assertNotIn('password', settings)

    def test_wrong_host_key_is_rejected_before_sending_password(self):
        self.configure(remember_password=True)
        attempts = self.fixture.attempts
        path = self.profile / 'connection.json'
        settings = json.loads(path.read_text('utf-8'))
        settings['host_key'] = f'[127.0.0.1]:{self.fixture.port} ssh-ed25519 {host_key().get_base64()}'
        path.write_text(json.dumps(settings), 'utf-8')
        with self.assertRaisesRegex(ValueError, '身份校验失败'):
            Remote(self.profile).call('state')
        self.assertEqual(self.fixture.attempts, attempts)

    def test_failed_password_keeps_working_connection_and_never_returns_secret(self):
        self.configure(remember_password=True)
        before = {p.name:p.read_bytes() for p in self.profile.iterdir() if p.is_file()}
        with self.assertRaisesRegex(ValueError, '用户名或密码不正确') as error:
            self.configure(password='wrong-fixture-password')
        self.assertNotIn('wrong-fixture-password', str(error.exception))
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.profile.iterdir() if p.is_file()})
        self.assertTrue(self.remote.call('state')['running'])

    def test_unchecking_remember_removes_saved_password_but_preserves_session(self):
        self.configure(remember_password=True)
        self.configure(password='', remember_password=False)
        self.assertEqual([p.name for p in self.profile.iterdir()], ['connection.json'])
        self.assertTrue(self.remote.call('state')['running'])
        self.assertFalse(Remote(self.profile).info()['has_password'])

    @unittest.skipUnless(os.name == 'nt' and os.environ.get('WEIYAN_TEST_BINARIES') == '1', 'Frozen Windows integration')
    def test_actual_frozen_controller_supports_password_and_pinned_host(self):
        self.configure(remember_password=True)
        root = Path(__file__).resolve().parents[2]
        executable = Path(os.environ.get('WEIYAN_CONTROL_EXECUTABLE',
                          str(root / 'build-output/未言诗集服务器控制端/未言诗集服务器控制端.exe')))
        output = self.profile / 'self-test.json'
        process = subprocess.Popen([str(executable), '--self-test', str(output), '--self-test-profile', str(self.profile)])
        self.assertEqual(process.wait(timeout=60), 0)
        report = json.loads(output.read_text('utf-8'))
        self.assertTrue(report['ok']);self.assertEqual(report['server_version'], '2.2.2')
        self.assertTrue(all(command == GATEWAY for command in self.fixture.commands))
        self.assertEqual(self.fixture.pty_requests, 0)


if __name__ == '__main__': unittest.main(verbosity=2)
