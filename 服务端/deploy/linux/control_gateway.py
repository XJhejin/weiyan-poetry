"""Forced-command SSH gateway. Install root-owned, outside the relay's data directory.

No shell, arbitrary paths, command strings, SQL, or management tokens cross this RPC.
The SSH key is additionally restricted with no-port/agent/X11-forwarding and no-pty.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, closing
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
from urllib.parse import urlsplit, parse_qs

from update import Updater, github_tls_context, version

BASE = Path('/opt/weiyan')
STORE = Path('/var/lib/weiyan-control')
DATA = Path('/var/lib/weiyan')
PUBLIC_ADDRESS = 'https://156.225.28.175:23074'
SERVICE = 'weiyan-server.service'
PROXY = 'weiyan-proxy.service'
ACTIVE_STAGES = {'queued', 'downloading', 'verifying', 'preparing', 'installing', 'rolling_back'}
SOURCE_FILES = {
    'server.py', 'relay_core.py', 'groups.py', 'friends.py', 'avatars.py', 'listening.py', 'migration.py',
    'release_config.py', 'requirements-headless.txt', 'admin.py', 'update.py',
    'admin_ui/index.html', 'admin_ui/admin.js', 'admin_ui/admin.css', 'admin_ui/motion.js',
    'admin_ui/logo.svg', 'admin_ui/logo.ico', 'deploy/linux/weiyan-server.service',
    'deploy/linux/server.env.example', 'deploy/linux/部署说明.md', 'deploy/linux/Caddyfile.example',
    'deploy/linux/weiyan-proxy.service', 'deploy/linux/control_gateway.py',
}


def run(args, timeout=30, check=True):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, check=check)


def current_version():
    return runpy.run_path(str(BASE / 'current/server/release_config.py'))['VERSION']


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8))
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def update_lock():
    import fcntl
    STORE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STORE / 'update.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('服务端正在更新，请等待本次更新完成。') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def progress(stage, message, percent=0, **extra):
    value = {'stage': stage, 'message': message, 'percent': percent, 'updated_at': int(time.time()), **extra}
    atomic_json(STORE / 'progress.json', value)
    return value


def update_status():
    path = STORE / 'progress.json'
    value = json.loads(path.read_text('utf-8')) if path.is_file() else {'stage': 'idle', 'message': '尚未执行远程更新。', 'percent': 0}
    if value['stage'] in ACTIVE_STAGES and value.get('unit'):
        if run(['systemctl', 'is-active', '--quiet', value['unit']], check=False).returncode:
            value = {**value, 'stage': 'error', 'message': '上次更新进程已停止，请检查运行状态后重试。'}
    return value


def admin_request(path, payload=None):
    access = (DATA / 'admin-url').read_text('utf-8').strip()
    parsed = urlsplit(access)
    token = parse_qs(parsed.query).get('token', [''])[0]
    if (parsed.scheme, parsed.netloc, parsed.path) != ('http', '127.0.0.1:23175', '/') or not re.fullmatch(r'[A-Za-z0-9_-]{40,64}', token):
        raise ValueError('远程管理入口无效，请重启聊天服务。')
    # Send the capability only to the fixed loopback destination, never to SSH stdout.
    headers = {'Cookie': 'weiyan_admin=' + token, 'X-Weiyan-Admin': 'desktop',
               'Origin': 'http://127.0.0.1:23175', 'Content-Type': 'application/json'}
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request('http://127.0.0.1:23175' + path, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=20) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('管理数据过大，请在服务器检查账号数量。')
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        raise ValueError(json.loads(error.read(4096)).get('error', '远程操作未完成。')) from error


def service_state(name):
    result = run(['systemctl', 'show', name, '--property=ActiveState,SubState,UnitFileState,MainPID'], check=False)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def host_state():
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        memory[key] = int(value.strip().split()[0]) * 1024
    disk = shutil.disk_usage(DATA)
    return {'address': PUBLIC_ADDRESS, 'hostname': os.uname().nodename, 'system': 'CentOS Linux',
            'version': current_version(), 'load': list(os.getloadavg()),
            'memory_total': memory['MemTotal'], 'memory_available': memory.get('MemAvailable', memory.get('MemFree', 0)),
            'disk_total': disk.total, 'disk_free': disk.free, 'service': service_state(SERVICE),
            'proxy': service_state(PROXY), 'update': update_status()}


class LinuxUpdater(Updater):
    @property
    def package_key(self):
        return 'server_linux_source'

    def info(self):
        return {**super().info(), 'current': current_version(), 'platform': 'Linux 远程服务端'}

    def check(self):
        result = super().check()
        available = version(result['latest']) > version(current_version())
        return {**result, **self.info(), 'available': available,
                'message': '发现新版本，可下载并安装。' if available else '当前已是最新正式版本。'}


def check_update():
    updater = LinuxUpdater('server', STORE)
    try:
        return updater.check()
    except ValueError as error:
        # Releases before 2.1.3 did not contain a signed Linux source asset.
        if '更新文件校验信息无效' in str(error):
            return {**updater.info(), 'available': False, 'latest': None,
                    'message': '当前正式发布尚未提供 Linux 更新包，新的正式版本发布后可在此安装。'}
        raise


def start_update():
    with update_lock():
        value = update_status()
        if value['stage'] in ACTIVE_STAGES:
            return {'started': True, **value}
        job = secrets.token_hex(12)
        unit = 'weiyan-update-' + job
        progress('queued', '已开始远程更新，关闭控制端不会中断。', unit=unit)
        try:
            run(['systemd-run', '--unit=' + unit, '--property=UMask=0077',
                 str(BASE / 'control/venv/bin/python'), str(BASE / 'control/control_gateway.py'), '--worker', job])
        except Exception:
            progress('error', '无法启动远程更新任务，原服务保持运行。')
            raise
        return {'started': True}


def unpack(archive, destination, release_version):
    """No extractall: require exactly the deployment allowlist and bounded regular files."""
    root = 'weiyan-server-' + release_version
    with tarfile.open(archive, 'r:gz') as bundle:
        seen, total = set(), 0
        for member in bundle:
            parts = PurePosixPath(member.name).parts
            relative = '/'.join(parts[1:])
            if (not member.isfile() or '\\' in member.name or not parts or parts[0] != root
                    or relative not in SOURCE_FILES or relative in seen or '..' in parts
                    or member.name != root + '/' + relative):
                raise ValueError('Linux 更新包包含不允许的文件或路径。')
            seen.add(relative)
            total += member.size
            if member.size < 0 or total > 20 * 1024 * 1024:
                raise ValueError('Linux 更新包解压大小超出限制。')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o644)
        if seen != SOURCE_FILES:
            raise ValueError('Linux 更新包缺少必要文件。')


def wait_healthy(expected, attempts=30):
    for _ in range(attempts):
        try:
            state = admin_request('/manage/state')
            if state.get('running') and state.get('version') == expected:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:23174/api/health', timeout=3) as response:
                    if response.status == 200:
                        return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise ValueError('新服务未通过启动检查。')


def swap_current(target):
    link = BASE / ('current.' + secrets.token_hex(8))
    link.symlink_to(target, target_is_directory=True)
    os.replace(link, BASE / 'current')


def activate(candidate, expected, job_dir):
    """Pause public traffic until health succeeds; restore code AND schema on failure."""
    previous = (BASE / 'current').resolve(strict=True)
    previous_version = current_version()
    was_running = admin_request('/manage/state')['running']
    if not was_running:
        raise ValueError('请先启动聊天服务，再执行版本更新。')
    proxy_was_active = service_state(PROXY).get('ActiveState') == 'active'
    stopped = switched = backup_complete = False
    try:
        if proxy_was_active:
            run(['systemctl', 'stop', PROXY])
        run(['systemctl', 'stop', SERVICE])
        stopped = True
        source_db = DATA / 'weiyan.sqlite3'
        with closing(sqlite3.connect(source_db)) as source, closing(sqlite3.connect(job_dir / 'database.before.sqlite3')) as backup:
            source.backup(backup)
        (job_dir / 'database.before.sqlite3').chmod(0o600)
        backup_complete = True
        atomic_json(job_dir / 'rollback.json', {'previous': str(previous), 'version': previous_version})
        swap_current(candidate)
        switched = True
        run(['systemctl', 'start', SERVICE])
        wait_healthy(expected)
        if proxy_was_active:
            run(['systemctl', 'start', PROXY])
    except Exception:
        progress('rolling_back', '更新未通过启动检查，正在恢复原程序和更新前数据库。')
        if stopped:
            run(['systemctl', 'stop', SERVICE], check=False)
            if switched:
                swap_current(previous)
            if backup_complete:
                target = DATA / 'weiyan.sqlite3'
                owner = target.stat()
                shutil.copyfile(job_dir / 'database.before.sqlite3', target)
                target.chmod(0o600)
                os.chown(target, owner.st_uid, owner.st_gid)
                for suffix in ('-wal', '-shm'):
                    target.with_name(target.name + suffix).unlink(missing_ok=True)
            run(['systemctl', 'start', SERVICE])
            wait_healthy(previous_version)
        if proxy_was_active:
            run(['systemctl', 'start', PROXY])
        raise


def perform_update(job):
    if not re.fullmatch(r'[0-9a-f]{24}', job):
        raise ValueError('无效更新任务。')
    with update_lock():
        unit = 'weiyan-update-' + job
        job_dir = STORE / job
        job_dir.mkdir(mode=0o700)
        # Runtime files must be traversable by the unprivileged relay account.
        # Job/database files remain under STORE (0700), with explicit 0600 writes.
        os.umask(0o022)
        try:
            progress('downloading', '正在获取签名更新清单。', unit=unit)
            updater = LinuxUpdater('server', STORE)
            info = updater.check()
            if not info['available']:
                progress('complete', info['message'], 100)
                return
            release = updater.release
            artifact = release['artifact']
            expected = 'weiyan-server-' + release['version'] + '-linux-source.tar.gz'
            if artifact['name'] != expected or artifact['bytes'] > 8 * 1024 * 1024:
                raise ValueError('Linux 更新文件无效。')
            # Construct the URL from the pinned repository and signed version/name.
            url = 'https://github.com/' + info['repository'] + '/releases/download/v' + release['version'] + '/' + expected
            request = urllib.request.Request(url, headers={'User-Agent': 'WeiyanPoetry-Control', 'Accept': 'application/octet-stream'})
            archive = job_dir / expected
            digest, size = hashlib.sha256(), 0
            with urllib.request.urlopen(request, timeout=35, context=github_tls_context()) as source, archive.open('xb') as output:
                while chunk := source.read(131072):
                    size += len(chunk)
                    if size > artifact['bytes']:
                        raise ValueError('下载文件大小异常。')
                    output.write(chunk)
                    digest.update(chunk)
                    progress('downloading', '正在下载远程服务端更新包。', size * 100 // artifact['bytes'], unit=unit)
            if size != artifact['bytes'] or digest.hexdigest() != artifact['sha256']:
                raise ValueError('更新包完整性校验失败，已阻止安装。')
            progress('verifying', '签名和 SHA-256 校验通过，正在检查部署文件。', unit=unit)
            candidate = BASE / 'versions' / (release['version'] + '-' + job)
            candidate.mkdir(parents=True)
            unpack(archive, candidate / 'server', release['version'])
            config = runpy.run_path(str(candidate / 'server/release_config.py'))
            if config['VERSION'] != release['version'] or config['REPOSITORY'] != info['repository']:
                raise ValueError('更新包版本或仓库不一致。')
            progress('preparing', '正在准备独立运行环境，聊天服务仍可使用。', unit=unit)
            run([str(BASE / 'python/bin/python3'), '-m', 'venv', str(candidate / 'venv')], timeout=90)
            python = str(candidate / 'venv/bin/python')
            run([python, '-m', 'pip', 'install', '--only-binary=:all:', '--disable-pip-version-check',
                 '-r', str(candidate / 'server/requirements-headless.txt')], timeout=600)
            run([python, '-m', 'compileall', '-q', str(candidate / 'server')], timeout=60)
            run([python, '-c', 'import nacl.bindings,certifi,PIL,simple_websocket'], timeout=30)
            progress('installing', '正在切换版本并检查启动状态，客户端将短暂重连。', unit=unit)
            activate(candidate, release['version'], job_dir)
            # The privileged gateway is updated only from the verified signed bundle.
            for source, name in ((candidate / 'server/deploy/linux/control_gateway.py', 'control_gateway.py'),
                                 (candidate / 'server/update.py', 'update.py'), (candidate / 'server/release_config.py', 'release_config.py')):
                temporary = BASE / 'control' / (name + '.new')
                shutil.copyfile(source, temporary)
                temporary.chmod(0o644)
                os.replace(temporary, BASE / 'control' / name)
            progress('complete', '远程服务端已更新至 ' + release['version'] + '，账号和未投递密文已保留。', 100)
        except Exception as error:
            progress('error', str(error) if isinstance(error, ValueError) else '更新未完成，请检查运行状态；原程序和数据库备份已保留。')
            raise


def dispatch(request):
    if not isinstance(request, dict) or set(request) - {'operation', 'payload'}:
        raise ValueError('控制请求格式无效。')
    operation = request.get('operation')
    if operation == 'state':
        return admin_request('/manage/state')
    if operation == 'host':
        return host_state()
    if operation == 'action':
        payload = request.get('payload')
        if not isinstance(payload, dict) or payload.get('action') not in {'start', 'stop', 'settings', 'disable', 'enable', 'disconnect', 'revoke', 'reset_avatar'}:
            raise ValueError('不允许执行此管理操作。')
        if payload['action'] == 'start':
            payload = {'action': 'start', 'host': '127.0.0.1', 'port': 23174}
        with update_lock():
            if update_status()['stage'] in ACTIVE_STAGES:
                raise ValueError('更新期间不能修改服务或账号设置。')
            return admin_request('/manage/action', payload)
    if operation == 'restart':
        with update_lock():
            if update_status()['stage'] in ACTIVE_STAGES:
                raise ValueError('更新期间不能重启服务。')
            run(['systemctl', 'restart', SERVICE])
            wait_healthy(current_version())
        return {'ok': True}
    if operation == 'update_info':
        return LinuxUpdater('server', STORE).info()
    if operation == 'update_check':
        return check_update()
    if operation == 'update_install':
        return start_update()
    if operation == 'update_status':
        return update_status()
    if operation == 'logs':
        # Relay logs contain no tokens or message bodies. Never return proxy/SSH logs.
        lines = run(['journalctl', '-u', SERVICE, '-n', '60', '--no-pager', '-o', 'short-iso']).stdout
        return {'text': re.sub(r'\?token=[A-Za-z0-9_-]+', '?token=[已隐藏]', lines[-16000:])}
    raise ValueError('不允许执行此控制操作。')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        perform_update(args.worker)
        return
    # sshd may pass SSH_ORIGINAL_COMMAND; it is deliberately never evaluated.
    try:
        raw = sys.stdin.buffer.readline(16385)
        if len(raw) > 16384:
            raise ValueError('控制请求过长。')
        result = {'ok': True, 'data': dispatch(json.loads(raw))}
    except Exception as error:
        result = {'ok': False, 'error': str(error) if isinstance(error, ValueError) else '无法读取远程服务，请检查运行状态或重启聊天服务。'}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode() + b'\n')


if __name__ == '__main__':
    main()
