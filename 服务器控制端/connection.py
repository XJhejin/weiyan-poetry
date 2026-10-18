"""Use the OS SSH client with a pinned host key and a forced-command identity."""
import json
import os
from pathlib import Path
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

    def identity(self):
        with self.guard:
            config = self.profile / 'connection.json'
            if not config.is_file():
                raise ValueError('此电脑尚未配置服务器管理授权。请按使用说明由管理员配置。')
            settings = json.loads(config.read_text('utf-8'))
            if (settings.get('host') != '156.225.28.175' or settings.get('port') != 22
                    or settings.get('user') != 'root' or not isinstance(settings.get('host_key'), str)):
                raise ValueError('连接配置无效。')
            if self.temporary is None:
                private_directory(self.profile)
                self.temporary = Path(tempfile.mkdtemp(prefix='session-', dir=self.profile))
                try:
                    private_directory(self.temporary)
                    key = protect((self.profile / 'identity.dpapi').read_bytes(), decrypt=True)
                    (self.temporary / 'identity').write_bytes(key)
                    private_file(self.temporary / 'identity')
                    (self.temporary / 'known_hosts').write_text(settings['host_key'].strip() + '\n', encoding='ascii')
                except Exception:
                    shutil.rmtree(self.temporary)
                    self.temporary = None
                    raise
            return settings, self.temporary

    def call(self, operation, payload=None):
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
            raise ValueError('无法连接服务器 SSH，请检查网络与服务器 22 端口。')
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
        with self.guard:
            if self.temporary:
                shutil.rmtree(self.temporary)
                self.temporary = None
