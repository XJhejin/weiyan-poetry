import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import select
import subprocess
import sys
import time
import zipfile

STARTED = time.monotonic()
BASE = Path('/opt/weiyan/forum-deploy')
ALLOWED = {'deploy_forum.py', 'configure_forum.php', 'verify_forum.php',
           'hushleaf.css', 'mobile.css', 'theme-header.htm', 'logo.svg',
           'wordmark.svg', 'LICENSE-notice.md', 'forum-config.json',
           'theme-footer.htm', 'theme-mobile-header.htm'}

def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)

def run(args, timeout=15):
    try:
        value = subprocess.run(args, capture_output=True, text=True, errors='replace', timeout=timeout)
        return {'code': value.returncode, 'stdout': value.stdout[-14000:], 'stderr': value.stderr[-1500:]}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'code': -1, 'error': type(error).__name__}

def diagnose():
    result = {'os': Path('/etc/os-release').read_text(), 'php': [], 'database': [],
              'nginx_sites': [], 'nginx_includes': []}
    for php in sorted(Path('/www/server/php').glob('*/bin/php')) + [Path('/usr/bin/php')]:
        if php.is_file():
            value = run([str(php), '-r', 'echo json_encode(["version"=>PHP_VERSION,"extensions"=>get_loaded_extensions()]);'])
            result['php'].append({'path': str(php), **value})
    for binary in ('/www/server/mysql/bin/mysql', '/www/server/mysql/bin/mysqld', '/usr/bin/mysql', '/usr/sbin/mysqld'):
        if Path(binary).is_file():
            result['database'].append({'path': binary, **run([binary, '--version'])})
    for folder in ('/www/server/panel/vhost/nginx', '/etc/nginx/conf.d'):
        for target in sorted(Path(folder).glob('*.conf')):
            safe = [line.strip() for line in target.read_text(errors='replace').splitlines()
                    if any(key in line for key in ('server_name ', 'listen ', 'root ', 'enable-php-', 'fastcgi_pass'))]
            result['nginx_sites'].append({'path': str(target), 'directives': safe[:15]})
    result['nginx_includes'] = [str(p) for p in Path('/www/server/nginx/conf').glob('enable-php-*.conf')]
    result['listeners'] = run(['ss', '-lnt'])
    result['services'] = run(['systemctl', 'show', 'weiyan-server.service', 'weiyan-proxy.service', 'mysqld.service', 'nginx.service', '--property=Id,ActiveState,SubState'])
    result['root_mysql_config_exists'] = Path('/root/.my.cnf').is_file()
    result['panel_database_exists'] = Path('/www/server/panel/data/default.db').is_file()
    result['forum_exists'] = Path('/opt/weiyan/forum').exists()
    result['database_config'] = []
    for config in (Path('/etc/my.cnf'), Path('/www/server/mysql/my.cnf')):
        if config.is_file():
            result['database_config'].append({'path': str(config), 'directives': [line.strip() for line in config.read_text(errors='replace').splitlines() if line.strip().startswith(('socket', 'datadir', 'port', 'basedir'))]})
    return result

def deploy(payload):
    raw = base64.b64decode(payload['source'], validate=True)
    if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != payload['sha256']:
        raise ValueError('Invalid deployment archive')
    destination = BASE / ('run-' + str(time.time_ns()))
    destination.mkdir(mode=0o700, parents=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if set(archive.namelist()) - ALLOWED:
            raise ValueError('Unexpected deployment file')
        for item in archive.infolist():
            if item.file_size > 1024 * 1024 or item.filename not in ALLOWED:
                raise ValueError('Invalid deployment file')
            (destination / item.filename).write_bytes(archive.read(item))
    command = [sys.executable, '-u', str(destination / 'deploy_forum.py')]
    process = subprocess.run(command, stdin=subprocess.DEVNULL)
    return {'exit_code': process.returncode, 'deployment': str(destination)}

os.umask(0o077)
emit({'ready': True, 'operations': ['diagnose', 'deploy', 'status', 'quit'], 'expires_minutes': 120})
emit({'id': 'initial-preflight', 'result': diagnose()})
while time.monotonic() - STARTED < 7200:
    if not select.select([sys.stdin], [], [], 15)[0]:
        continue
    line = sys.stdin.readline(4 * 1024 * 1024)
    if not line:
        break
    request_id = None
    try:
        request = json.loads(line)
        request_id = request.get('id')
        action = request['operation']
        if action == 'quit':
            emit({'id': request_id, 'ok': True, 'closed': True})
            break
        if action == 'diagnose':
            result = diagnose()
        elif action == 'deploy':
            result = deploy(request['payload'])
        elif action == 'status':
            report = Path('/opt/weiyan/forum-deploy/deployment.json')
            result = json.loads(report.read_text()) if report.is_file() else {'stage': 'not_deployed'}
        else:
            raise ValueError('Operation is not permitted')
        emit({'id': request_id, 'ok': True, 'result': result})
    except Exception as error:
        emit({'id': request_id, 'ok': False, 'error': str(error)[:1000]})
