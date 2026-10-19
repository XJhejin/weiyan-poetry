"""Use the OS SSH client with a pinned host key and a forced-command identity."""
import json
import base64
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import secrets
import time
from collections import deque
import shutil
import subprocess
import tempfile
import threading

from credentials import protect, private_directory, private_file, profile_directory


class Remote:
    def __init__(self, profile=None):
        self.profile = Path(profile) if profile else profile_directory()
        self.temporary = None
        self.guard = threading.Lock()
        self.calls = threading.RLock()
        self.samples = deque(maxlen=30)

    @staticmethod
    def validate(settings):
        host, port, user = settings.get('host'), settings.get('port'), settings.get('user')
        if not isinstance(host, str) or not host or len(host) > 253:
            raise ValueError('请填写服务器 IP 地址或域名，不含协议和端口。')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host) or '..' in host:
                raise ValueError('服务器地址无效，请单独填写地址和 SSH 端口。')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('SSH 端口必须为 1 至 65535。')
        if not isinstance(user, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', user):
            raise ValueError('SSH 用户名无效。')
        return {'host': host, 'port': port, 'user': user}

    @staticmethod
    def key_line(settings, line):
        fields = line.strip().split() if isinstance(line, str) else []
        if len(fields) != 3 or fields[1] != 'ssh-ed25519':
            raise ValueError('请先读取并核对服务器 ED25519 主机指纹。')
        host = settings['host'] if settings['port'] == 22 else f"[{settings['host']}]:{settings['port']}"
        if fields[0] != host:
            raise ValueError('主机公钥与连接地址不匹配。')
        try:
            raw = base64.b64decode(fields[2], validate=True)
            if len(raw) != 51 or raw[:15] != b'\0\0\0\x0bssh-ed25519':
                raise ValueError()
        except ValueError as error:
            raise ValueError('服务器主机公钥无效。') from error
        return ' '.join(fields), 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')

    def info(self):
        settings = {'host': '156.225.28.175', 'port': 22, 'user': 'root'}
        path = self.profile / 'connection.json'
        if path.is_file():
            value = json.loads(path.read_text('utf-8'))
            settings = self.validate(value)
            settings['host_key'] = value.get('host_key', '')
            try: settings['fingerprint'] = self.key_line(settings, settings['host_key'])[1]
            except ValueError: settings['fingerprint'] = ''
            settings['has_identity'] = self.key_file(value).is_file()
        else:
            settings.update(has_identity=False, host_key='', fingerprint='')
        return settings

    def key_file(self, settings):
        name = settings.get('identity_file', 'identity.dpapi')
        if not isinstance(name, str) or not re.fullmatch(r'identity(?:-[0-9a-f]{24})?\.dpapi', name):
            raise ValueError('管理授权路径无效。')
        return self.profile / name

    def scan(self, payload):
        settings = self.validate(payload)
        scanner = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/OpenSSH/ssh-keyscan.exe'
        try:
            result = subprocess.run([str(scanner), '-T', '8', '-p', str(settings['port']), '-t', 'ed25519', settings['host']],
                                    capture_output=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error:
            raise ValueError('读取主机指纹超时，请检查服务器地址和 SSH 端口。') from error
        lines = [line for line in result.stdout.decode('ascii', errors='replace').splitlines() if line and not line.startswith('#')]
        if not lines:
            raise ValueError('无法读取服务器主机指纹，请检查网络、SSH 端口和服务器 ED25519 主机密钥。')
        line, fingerprint = self.key_line(settings, lines[0])
        return {**settings, 'host_key': line, 'fingerprint': fingerprint}

    def configure(self, payload):
        settings = self.validate(payload)
        line, _ = self.key_line(settings, payload.get('host_key'))
        current = self.info()
        same = all(current.get(k) == settings[k] for k in ('host', 'port')) and current.get('host_key') == line
        if not same and payload.get('trust_host') is not True:
            raise ValueError('请通过可信渠道核对主机指纹，再确认信任此服务器。')
        private = payload.get('private_key', '')
        if not isinstance(private, str) or len(private) > 32768:
            raise ValueError('管理密钥格式无效。')
        if private:
            if not private.strip().startswith('-----BEGIN OPENSSH PRIVATE KEY-----'):
                raise ValueError('请选择未加口令的 OpenSSH 管理私钥；本机会用 Windows 账号加密保存。')
            encrypted = protect(private.strip().encode() + b'\n')
        else:
            path = self.profile / 'connection.json'
            if not path.is_file() or not current['has_identity']:
                raise ValueError('此电脑尚未配置授权，请选择已在服务器登记的管理私钥。')
            encrypted = self.key_file(json.loads(path.read_text('utf-8'))).read_bytes()
        private_directory(self.profile)
        # Verify credentials in isolation before changing a working connection.
        with tempfile.TemporaryDirectory(prefix='connect-', dir=self.profile) as folder:
            candidate = Path(folder)
            private_directory(candidate)
            (candidate / 'identity.dpapi').write_bytes(encrypted)
            candidate_settings = {**settings, 'host_key': line}
            (candidate / 'connection.json').write_text(json.dumps(candidate_settings), 'utf-8')
            remote = Remote(candidate)
            try: state = remote.call('state')
            finally: remote.close()
            if not isinstance(state, dict) or not isinstance(state.get('running'), bool):
                raise ValueError('该服务器没有返回有效的未言诗集管理状态，请先安装受限管理组件。')
        with self.calls, self.guard:
            if self.temporary:
                shutil.rmtree(self.temporary)
                self.temporary = None
            name = 'identity-' + secrets.token_hex(12) + '.dpapi'
            (self.profile / name).write_bytes(encrypted)
            private_file(self.profile / name)
            settings.update(host_key=line, identity_file=name)
            temporary = self.profile / ('connection-' + secrets.token_hex(8) + '.tmp')
            temporary.write_text(json.dumps(settings, ensure_ascii=False), 'utf-8')
            os.replace(temporary, self.profile / 'connection.json')
            self.samples.clear()
        return {'ok': True, 'connection': self.info(), 'running': state['running']}

    def identity(self):
        with self.guard:
            config = self.profile / 'connection.json'
            if not config.is_file():
                raise ValueError('此电脑尚未配置服务器管理授权。请按使用说明由管理员配置。')
            settings = json.loads(config.read_text('utf-8'))
            self.validate(settings)
            self.key_line(settings, settings.get('host_key'))
            if self.temporary is None:
                private_directory(self.profile)
                self.temporary = Path(tempfile.mkdtemp(prefix='session-', dir=self.profile))
                try:
                    private_directory(self.temporary)
                    key = protect(self.key_file(settings).read_bytes(), decrypt=True)
                    (self.temporary / 'identity').write_bytes(key)
                    private_file(self.temporary / 'identity')
                    (self.temporary / 'known_hosts').write_text(settings['host_key'].strip() + '\n', encoding='ascii')
                except Exception:
                    shutil.rmtree(self.temporary)
                    self.temporary = None
                    raise
            return settings, self.temporary

    def call(self, operation, payload=None):
        with self.calls:
            started, success = time.perf_counter(), False
            try:
                result = self._call(operation, payload)
                success = True
                return result
            finally:
                if operation == 'state':
                    self.samples.append((round((time.perf_counter() - started) * 1000, 1) if success else None, time.time()))

    def network(self):
        # A snapshot must not wait behind a timed-out management connection.
        values = list(self.samples)
        return {'latency_ms': values[-1][0] if values else None,
                'loss_rate': round(100 * sum(s[0] is None for s in values) / len(values), 1) if values else None,
                'samples': len(values), 'window': 30, 'measurement': 'ssh-management-request'}

    def _call(self, operation, payload=None):
        settings, directory = self.identity()
        ssh = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/OpenSSH/ssh.exe'
        if not ssh.is_file():
            raise ValueError('Windows OpenSSH 客户端未安装，请在系统可选功能中启用 OpenSSH 客户端。')
        args = [str(ssh), '-F', 'NUL', '-T', '-p', str(settings['port']), '-i', str(directory / 'identity'),
                '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'UserKnownHostsFile=' + str(directory / 'known_hosts'), '-o', 'GlobalKnownHostsFile=NUL',
                '-o', 'ConnectTimeout=12', '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=10',
                '-o', 'ServerAliveCountMax=2', settings['user'] + '@' + settings['host'], 'weiyan-control']
        request = json.dumps({'operation': operation, 'payload': payload}, ensure_ascii=False).encode('utf-8') + b'\n'
        try:
            result = subprocess.run(args, input=request, capture_output=True, timeout=100,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error:
            raise ValueError('远程请求超时，请检查网络后重试。服务器上的更新任务会继续运行。') from error
        if result.returncode:
            detail = result.stderr.decode('utf-8', errors='replace')
            if 'Host key verification failed' in detail or 'REMOTE HOST IDENTIFICATION' in detail:
                raise ValueError('服务器身份校验失败，已停止连接。请核对服务器主机密钥。')
            if 'Permission denied' in detail:
                raise ValueError('管理授权无效或已被撤销，请重新配置管理授权。')
            raise ValueError(f"无法连接服务器 SSH（{settings['host']}:{settings['port']}），请检查网络、端口及管理授权。")
        if len(result.stdout) > 16 * 1024 * 1024:
            raise ValueError('远程响应过大。')
        try:
            response = json.loads(result.stdout)
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError('远程控制组件返回了无效数据。') from error
        if not response.get('ok'):
            raise ValueError(response.get('error', '远程操作未完成。'))
        return response['data']

    def close(self):
        with self.calls, self.guard:
            if self.temporary:
                shutil.rmtree(self.temporary)
                self.temporary = None
