"""Install an isolated native Discuz! site without changing existing application data."""
from __future__ import annotations

import argparse
import base64
import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
BASE = Path('/opt/weiyan/forum')
CURRENT = BASE / 'current'
STATE = Path('/opt/weiyan/forum-deploy')
SETTINGS = Path('/etc/weiyan-forum')
PRIVATE = SETTINGS / 'secrets.json'
PHP = '/www/server/php/82/bin/php'
FPM = '/www/server/php/82/sbin/php-fpm'
CADDY = '/opt/weiyan/caddy/caddy'
CADDY_CONFIG = Path('/etc/weiyan/Caddyfile')
NGINX = '/www/server/nginx/sbin/nginx'
NGINX_CONFIG = Path('/www/server/panel/vhost/nginx/bbs.hushleaf.top.conf')
DOMAIN = 'bbs.hushleaf.top'
REPORT = STATE / 'deployment.json'
DB_SOCKET = '/var/lib/weiyan-forum-db/run/mysql.sock'


def emit(**value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.pending')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temporary.chmod(0o600)
    os.replace(temporary, path)


def run(args, *, input=None, timeout=120):
    result = subprocess.run(args, input=input, capture_output=True, timeout=timeout)
    if result.returncode:
        # PHP initialization contains credentials; its full diagnostics stay root-only.
        log = STATE / ('error-' + str(time.time_ns()) + '.log')
        log.write_bytes(result.stdout + b'\n' + result.stderr)
        log.chmod(0o600)
        detail = (result.stdout + b'\n' + result.stderr).decode('utf-8', errors='replace')
        if PRIVATE.is_file():
            for value in json.loads(PRIVATE.read_text()).values():
                if isinstance(value, str) and value:
                    detail = detail.replace(value, '[redacted]')
        if input:
            try:
                for value in json.loads(input).values():
                    if isinstance(value, str) and value:
                        detail = detail.replace(value, '[redacted]')
            except (ValueError, AttributeError):
                pass
        detail = re.sub(r'[A-Za-z0-9+/=_-]{60,}', '[encoded data omitted]', detail)
        raise RuntimeError('Command failed: ' + Path(args[0]).name + '; ' + detail[-4000:])
    return result.stdout


def progress(stage):
    atomic_json(REPORT, {'stage': stage, 'time': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    emit(stage=stage)


def fetch_source(config):
    archive = STATE / ('DiscuzX-' + config['commit'] + '.tar.gz')
    expected = config['source_archive_sha256']
    if not archive.is_file() or hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() != expected:
        request = urllib.request.Request('https://codeload.github.com/DiscuzTeam/DiscuzX/tar.gz/' + config['commit'], headers={'User-Agent': 'HushLeaf-Forum-Deploy'})
        pending = archive.with_suffix('.partial')
        with urllib.request.urlopen(request, timeout=60) as source, pending.open('wb') as output:
            total = 0
            while chunk := source.read(262144):
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    raise ValueError('Unexpected upstream archive size')
                output.write(chunk)
        with pending.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('Upstream SHA-256 mismatch')
        os.replace(pending, archive)
    destination = BASE / 'releases' / config['commit']
    if not destination.is_dir():
        destination.mkdir(parents=True, mode=0o755)
        prefix = 'DiscuzX-' + config['commit'] + '/upload/'
        with tarfile.open(archive, 'r:gz') as bundle:
            for entry in bundle:
                if not entry.name.startswith(prefix):
                    continue
                relative = entry.name[len(prefix):]
                if not relative:
                    continue
                target = destination / relative
                if '..' in Path(relative).parts or '\\' in relative or not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('Unsafe upstream archive path')
                if entry.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o755)
                elif entry.isfile() and entry.size < 20 * 1024 * 1024:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with bundle.extractfile(entry) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o644)
                else:
                    raise ValueError('Unexpected upstream archive member')
    return destination


def php(action, payload=None):
    return run([PHP, '-d', 'display_errors=0', '-d', 'zend.exception_ignore_args=1', '-d', 'mysqli.default_socket=/tmp/mysql.sock',
                str(HERE / 'configure_forum.php'), action],
               input=json.dumps(payload).encode() if payload else None, timeout=300)


def install_database():
    """Run a separate, socket-only instance; never modify the panel database."""
    for name, home in [('weiyan-forum', '/var/lib/weiyan-forum'),
                       ('weiyan-forum-db', '/var/lib/weiyan-forum-db')]:
        try:
            pwd.getpwnam(name)
        except KeyError:
            run(['useradd', '--system', '--home-dir', home, '--shell', '/sbin/nologin', name])
    account = pwd.getpwnam('weiyan-forum-db')
    forum = pwd.getpwnam('weiyan-forum')
    directory = Path('/var/lib/weiyan-forum-db')
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(directory, account.pw_uid, forum.pw_gid)
    for name, mode in [('data', 0o700), ('run', 0o750)]:
        path = directory / name
        path.mkdir(mode=mode, exist_ok=True)
        os.chown(path, account.pw_uid, forum.pw_gid)
    binary = '/www/server/mysql/bin/mysqld'
    cnf = f'''[mysqld]
basedir=/www/server/mysql
datadir={directory}/data
socket={DB_SOCKET}
pid-file={directory}/run/mysql.pid
log-error={directory}/mysql.log
skip-networking
skip-symbolic-links
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
default-storage-engine=InnoDB
innodb_buffer_pool_size=128M
innodb_log_file_size=32M
innodb_flush_log_at_trx_commit=1
max_connections=30
table_open_cache=256
performance_schema=OFF
sql_mode=NO_ENGINE_SUBSTITUTION
'''
    (SETTINGS / 'mysqld.cnf').write_text(cnf, 'utf-8')
    (SETTINGS / 'mysqld.cnf').chmod(0o644)
    if not (directory / 'data/mysql').is_dir():
        run(['runuser', '-u', 'weiyan-forum-db', '--', binary,
             '--defaults-file=' + str(SETTINGS / 'mysqld.cnf'), '--initialize-insecure'], timeout=180)
    unit = f'''[Unit]
Description=HushLeaf forum isolated MySQL
After=network.target
[Service]
Type=simple
User=weiyan-forum-db
Group=weiyan-forum
UMask=0007
ExecStart={binary} --defaults-file=/etc/weiyan-forum/mysqld.cnf
Restart=on-failure
RestartSec=3
LimitNOFILE=8192
ProtectHome=true
ProtectSystem=full
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
'''
    Path('/etc/systemd/system/weiyan-forum-db.service').write_text(unit, 'utf-8')
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'weiyan-forum-db.service'])
    for attempt in range(40):
        if Path(DB_SOCKET).exists():
            os.chmod(DB_SOCKET, 0o770)
            return
        time.sleep(1)
    raise RuntimeError('Isolated forum database did not start')


def theme():
    source = CURRENT / 'template/discuzx5'
    target = CURRENT / 'template/hushleaf'
    shutil.copytree(source, target, dirs_exist_ok=True)
    (target / 'common').mkdir(exist_ok=True)
    for name in ('logo.svg', 'wordmark.svg', 'hushleaf.css', 'mobile.css'):
        destination = CURRENT / 'static/hushleaf' / name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copyfile(HERE / name, destination)
        destination.chmod(0o644)
    shutil.copyfile(HERE / 'wordmark.svg', target / 'static/logo.svg')
    (target / 'static/logo.svg').chmod(0o644)
    header = (source / 'common/header.php').read_text('utf-8-sig')
    header = header.replace('</head>', '<link rel="stylesheet" href="static/hushleaf/hushleaf.css?v=20261025">\n</head>')
    header += '\n' + (HERE / 'theme-header.htm').read_text('utf-8')
    (target / 'common/header.php').write_text(header, 'utf-8')
    # The stock mobile templates retain all native mobile navigation and posting.
    mobile = target / 'touch/common'
    mobile.mkdir(parents=True, exist_ok=True)
    mobile_header = (CURRENT / 'template/default/touch/common/header.php').read_text('utf-8-sig')
    mobile_header = mobile_header.replace('</head>', '<link rel="stylesheet" href="static/hushleaf/mobile.css?v=20261025">\n</head>')
    (mobile / 'header.php').write_text(mobile_header, 'utf-8')
    mobile_forum = (CURRENT / 'template/default/touch/forum/discuz.php').read_text('utf-8-sig')
    intro = '''<section class="hl-mobile-intro"><img src="static/hushleaf/logo.svg" alt=""><small>HUSHLEAF COMMUNITY</small><h1>把未言之意，<br>写成相逢。</h1><p>写下所思，分享所见。<br>在这里，与同路的掇花者相遇。</p></section>'''
    mobile_forum = mobile_forum.replace('<!--[diy=diy1]-->', intro + '\n<!--[diy=diy1]-->', 1)
    (target / 'touch/forum').mkdir(exist_ok=True)
    (target / 'touch/forum/discuz.php').write_text(mobile_forum, 'utf-8')
    style = json.loads((source / 'discuz_style_discuzx5.json').read_text('utf-8-sig'))
    data = style['Data']
    data.update(name='未言诗集 · HushLeaf', tplname='未言诗集 · HushLeaf',
                directory='./template/hushleaf', copyright='未言诗集 / Discuz!')
    for key in ('setting', 'defaultindex', 'diy'):
        data.pop(key, None)
    data['style'].update({
        'bgcolor':'#fcf8fa','menubgcolor':'#a96788','menuhoverbgcolor':'#91536f',
        'menucurbgcolor':'#91536f','menutext':'#91536f','highlightlink':'#a96788',
        'link':'#403640','tabletext':'#403640','midtext':'#7d6b79','lighttext':'#a28e9c',
        'commonbg':'#fcf8fa','commonborder':'#eee1e8','specialbg':'#f5e7ef',
        'specialborder':'#eee1e8','titlebgcolor':'#f8f0f5','contentseparate':'#f5eaf0',
        'wrapbordercolor':'#eee1e8','inputborder':'#e6d7e1',
        'styleimgdir':'template/hushleaf/static/','boardimg':'logo.svg',
        'touchimg':'static/hushleaf/wordmark.svg','searchimg':'static/hushleaf/wordmark.svg',
        'btnbg':'#f4e8ef','btntxt':'#91536f','btnbga':'#a96788','btntxta':'#ffffff'})
    for name, value in {'top_nav_widthauto':'1', 'top_nav_dark':'0',
                        'is_search':'1','top_navnum':'6','touch_style_bodybg':'#fcf8fa',
                        'touch_style_color':'#a96788','touch_style_border':'#eee1e8',
                        'bottom_qrcode':'','bottom_qrcodetxt':'','sider_wechat_qrcode':'',
                        'bottom_txt':'社区交流与使用反馈，请前往「意见与回声」版块。',
                        'admin_color':'#a96788'}.items():
        if name in data.get('var', {}):
            data['var'][name]['value'] = value
    for old in target.glob('discuz_style_*'):
        old.unlink()
    (target / 'discuz_style_hushleaf.json').write_text(json.dumps(style, ensure_ascii=False), 'utf-8')
    for path in target.rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o644)


def install_runtime():
    try:
        account = pwd.getpwnam('weiyan-forum')
    except KeyError:
        run(['useradd', '--system', '--home-dir', '/var/lib/weiyan-forum', '--shell', '/sbin/nologin', 'weiyan-forum'])
        account = pwd.getpwnam('weiyan-forum')
    runtime = Path('/var/lib/weiyan-forum')
    for path in (runtime, runtime / 'sessions', runtime / 'uploads'):
        path.mkdir(parents=True, exist_ok=True, mode=0o750)
        os.chown(path, account.pw_uid, account.pw_gid)
    config = f'''[global]
pid = /var/lib/weiyan-forum/php-fpm.pid
error_log = /var/lib/weiyan-forum/php-fpm.log
daemonize = no
[hushleaf]
user = weiyan-forum
group = weiyan-forum
listen = 127.0.0.1:23274
listen.allowed_clients = 127.0.0.1
pm = ondemand
pm.max_children = 6
pm.process_idle_timeout = 20s
pm.max_requests = 500
clear_env = yes
security.limit_extensions = .php
catch_workers_output = yes
php_admin_flag[display_errors] = off
php_admin_flag[log_errors] = on
php_admin_value[error_reporting] = 22527
php_admin_value[error_log] = /var/lib/weiyan-forum/php-errors.log
php_admin_value[session.save_path] = /var/lib/weiyan-forum/sessions
php_admin_value[upload_tmp_dir] = /var/lib/weiyan-forum/uploads
php_admin_value[memory_limit] = 192M
php_admin_value[upload_max_filesize] = 64M
php_admin_value[post_max_size] = 66M
php_admin_value[max_execution_time] = 120
php_admin_value[date.timezone] = Asia/Hong_Kong
php_admin_value[mysqli.default_socket] = {DB_SOCKET}
php_admin_value[open_basedir] = /opt/weiyan/forum/:/var/lib/weiyan-forum/:/var/lib/weiyan-forum-db/run/:/tmp/:/www/server/php/82/:/dev/urandom
'''
    (SETTINGS / 'php-fpm.conf').write_text(config, 'utf-8')
    (SETTINGS / 'php-fpm.conf').chmod(0o644)
    run([FPM, '-t', '--fpm-config', str(SETTINGS / 'php-fpm.conf')])
    service = f'''[Unit]
Description=HushLeaf Discuz PHP worker
After=network.target
[Service]
Type=simple
ExecStart={FPM} --nodaemonize --fpm-config /etc/weiyan-forum/php-fpm.conf
Restart=on-failure
RestartSec=3
UMask=0022
PrivateTmp=false
ProtectHome=true
ProtectSystem=full
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
'''
    Path('/etc/systemd/system/weiyan-forum-php.service').write_text(service, 'utf-8')
    for name in ('data', 'avatar'):
        directory = CURRENT / name
        for path in [directory, *directory.rglob('*')]:
            os.chown(path, account.pw_uid, account.pw_gid)
            path.chmod(0o755 if path.is_dir() else 0o644)
    # Native template/plugin installation remains available in the admin panel.
    for name in ('template', 'source/plugin'):
        directory = CURRENT / name
        for path in [directory, *directory.rglob('*')]:
            os.chown(path, account.pw_uid, account.pw_gid)
            path.chmod(0o755 if path.is_dir() else 0o644)
    config_dir = CURRENT / 'config'
    os.chown(config_dir, 0, account.pw_gid)
    config_dir.chmod(0o750)
    for path in config_dir.glob('config_*.php'):
        os.chown(path, 0, account.pw_gid)
        path.chmod(0o640)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'weiyan-forum-php.service'])
    run(['systemctl', 'restart', 'weiyan-forum-php.service'])


def site_block():
    return '''# BEGIN HushLeaf Discuz forum
https://bbs.hushleaf.top {
    tls {
        issuer acme {
            dir https://acme-v02.api.letsencrypt.org/directory
            disable_http_challenge
        }
    }
    root * /opt/weiyan/forum/current
    encode zstd gzip
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
        X-Frame-Options SAMEORIGIN
        -Server
    }
    @private path /config/* /install /install/* /source/* /vendor/* /.git/* /data/log/* /data/sysdata/* /data/template/* /data/backup* /data/cache/*.php /data/cache/*.json /*.sql /*.bak /*.ini
    respond @private 404
    @uploaded_php path_regexp uploaded_php ^/(data|static)/.*\\.(php[0-9]?|phtml|phar)(/|$)
    respond @uploaded_php 404
    php_fastcgi 127.0.0.1:23274 {
        env HTTPS on
        env HTTP_X_FORWARDED_PROTO https
        env SERVER_PORT 443
    }
    file_server
}
# END HushLeaf Discuz forum
'''


def activate_site(repair_ip_renewal=False):
    old_config = CADDY_CONFIG.read_bytes()
    old_nginx = NGINX_CONFIG.read_bytes() if NGINX_CONFIG.is_file() else None
    backup = STATE / ('Caddyfile.before-forum-' + str(time.time_ns()))
    backup.write_bytes(old_config)
    candidate = re.sub(r'# BEGIN HushLeaf Discuz forum\n.*?# END HushLeaf Discuz forum\n?', '', old_config.decode(), flags=re.S).rstrip() + '\n\n' + site_block()
    if repair_ip_renewal:
        candidate = candidate.replace('profile shortlived\n            disable_tlsalpn_challenge',
                                      'profile shortlived\n            disable_http_challenge')
    trial = STATE / 'Caddyfile.candidate'
    trial.write_text(candidate, 'utf-8')
    trial.chmod(0o644)
    run(['runuser','-u','weiyan-proxy','--','env','HOME=/var/lib/weiyan-proxy',
         'WEIYAN_PUBLIC_IP=156.225.28.175', CADDY,'validate','--config',str(trial),'--adapter','caddyfile'])
    nginx = 'server {\n    listen 80;\n    server_name bbs.hushleaf.top;\n    return 301 https://bbs.hushleaf.top$request_uri;\n}\n'
    try:
        NGINX_CONFIG.write_text(nginx, 'utf-8'); NGINX_CONFIG.chmod(0o644)
        run([NGINX, '-t'])
        CADDY_CONFIG.write_text(candidate, 'utf-8')
        run(['systemctl', 'restart', 'weiyan-proxy.service'])
        run([NGINX, '-s', 'reload'])
        for attempt in range(30):
            try:
                with urllib.request.urlopen('https://' + DOMAIN + '/forum.php', timeout=10) as response:
                    page = response.read(2 * 1024 * 1024).decode('utf-8')
                    if response.status == 200 and '未言诗集' in page and 'hl-hero' in page:
                        break
            except Exception:
                pass
            time.sleep(4)
        else:
            raise RuntimeError('Public HTTPS forum did not pass startup verification')
        for url, marker in [('https://www.hushleaf.top/release.json', b'2.2.2'),
                            ('https://156.225.28.175:23074/api/health', b'"ok"')]:
            for attempt in range(45):
                try:
                    with urllib.request.urlopen(url, timeout=15) as response:
                        if marker in response.read(16384):
                            break
                except Exception:
                    pass
                time.sleep(2)
            else:
                raise RuntimeError('An existing application failed verification: ' + url)
    except Exception:
        CADDY_CONFIG.write_bytes(old_config)
        if old_nginx is None:
            NGINX_CONFIG.unlink(missing_ok=True)
        else:
            NGINX_CONFIG.write_bytes(old_nginx)
        run(['systemctl', 'restart', 'weiyan-proxy.service'])
        run([NGINX, '-s', 'reload'])
        raise


def backups():
    """Create a restorable database + attachments snapshot; only this forum is read."""
    directory = Path('/var/backups/weiyan-forum')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    dump = directory / ('forum-' + stamp + '.sql.gz')
    command = ['/www/server/mysql/bin/mysqldump', '--defaults-file=/etc/weiyan-forum/mysql.cnf',
               '--single-transaction', '--quick', '--hex-blob', '--no-tablespaces', 'hushleaf_bbs']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with gzip.open(dump, 'wb') as output:
        shutil.copyfileobj(process.stdout, output)
    error = process.stderr.read(); code = process.wait()
    if code:
        dump.unlink(missing_ok=True)
        raise RuntimeError('Forum database backup did not complete')
    dump.chmod(0o600)
    with tarfile.open(directory / ('forum-' + stamp + '.files.tar.gz'), 'w:gz') as archive:
        for relative in ('config', 'data/attachment', 'data/avatar', 'avatar', 'template/hushleaf'):
            if (CURRENT / relative).exists():
                archive.add(CURRENT / relative, arcname=relative)
    for file in directory.glob('forum-*'):
        file.chmod(0o600)
    # Rotation concerns backup snapshots only; live forum content is not expired.
    snapshots = sorted(directory.glob('forum-*.sql.gz'))
    for old in snapshots[:-14]:
        name = old.name.removesuffix('.sql.gz')
        old.unlink()
        (directory / (name + '.files.tar.gz')).unlink(missing_ok=True)


def backup_service():
    secrets_value = json.loads(PRIVATE.read_text())
    client = '[client]\nuser=hushleaf_bbs\npassword=' + secrets_value['db_password'] + '\nsocket=' + DB_SOCKET + '\n'
    (SETTINGS / 'mysql.cnf').write_text(client, 'utf-8')
    (SETTINGS / 'mysql.cnf').chmod(0o600)
    Path('/etc/systemd/system/weiyan-forum-backup.service').write_text(
        '[Unit]\nDescription=HushLeaf forum database and attachment backup\n[Service]\nType=oneshot\nUMask=0077\nExecStart=' + sys.executable + ' ' + str(Path(__file__).resolve()) + ' --backup\n', 'utf-8')
    Path('/etc/systemd/system/weiyan-forum-backup.timer').write_text(
        '[Unit]\nDescription=Daily HushLeaf forum backup\n[Timer]\nOnCalendar=*-*-* 03:30:00\nPersistent=true\n[Install]\nWantedBy=timers.target\n', 'utf-8')
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now','weiyan-forum-backup.timer'])
    backups()


def main():
    os.umask(0o022)
    SETTINGS.mkdir(mode=0o755, parents=True, exist_ok=True)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o755)  # Caddy's unprivileged validation can read its public candidate.
    config = json.loads((HERE / 'forum-config.json').read_text())
    if config.get('operation') == 'diagnose':
        log = run(['journalctl', '-u', 'weiyan-proxy.service', '-n', '30', '--no-pager']).decode(errors='replace')
        emit(tls_config=[line for line in CADDY_CONFIG.read_text().splitlines()
                         if any(word in line for word in ('tls', 'profile', 'issuer', 'challenge', 'https://', 'renew'))],
             certificate_log=log[-6000:],
             services=run(['systemctl', 'show', 'weiyan-forum-db.service', 'weiyan-forum-php.service',
                           'weiyan-forum-backup.timer', 'weiyan-server.service', 'weiyan-proxy.service',
                           '--property=Id,ActiveState,SubState']).decode(),
             backups=[{'name':p.name,'bytes':p.stat().st_size}
                      for p in sorted(Path('/var/backups/weiyan-forum').glob('forum-*.sql.gz'))],
             php_errors=(Path('/var/lib/weiyan-forum/php-errors.log').stat().st_size
                         if Path('/var/lib/weiyan-forum/php-errors.log').is_file() else 0))
        return
    for path in (HERE / 'configure_forum.php',):
        run([PHP,'-l',str(path)])
    progress('starting_isolated_forum_database')
    install_database()
    initialized = STATE / 'initialized.json'
    if not initialized.is_file():
        progress('downloading_official_discuz')
        source = fetch_source(config)
        if CURRENT.exists() and CURRENT.resolve() != source:
            raise ValueError('An unrelated forum installation already exists')
        if not CURRENT.exists():
            CURRENT.symlink_to(source, target_is_directory=True)
        if not PRIVATE.is_file():
            atomic_json(PRIVATE, {'db_password':secrets.token_urlsafe(36), 'admin_password':secrets.token_urlsafe(21)})
        value = json.loads(PRIVATE.read_text())
        if 'db_root_password' not in value:
            value['db_root_password'] = secrets.token_urlsafe(36)
            atomic_json(PRIVATE, value)
        database_marker = STATE / 'database-created.json'
        if not database_marker.is_file():
            progress('creating_separate_forum_database')
            php('database')
            atomic_json(database_marker, {'created':True})
        if not (CURRENT / 'data/install.lock').is_file():
            progress('running_official_installer')
            php('install-config')
            if not (CURRENT / 'config/config_global.php').is_file():
                raise RuntimeError('Official installer did not generate its configuration')
            php('install-schema')
            php('harden-config')
        progress('applying_hushleaf_theme')
        theme()
        php('configure')
        verification = json.loads(php('verify'))
        if not verification.get('ok'):
            raise RuntimeError('Native forum schema verification failed')
        atomic_json(initialized, {'commit':config['commit'],'installed':True})
    else:
        progress('updating_theme')
        theme(); php('theme')
        verification = json.loads(php('verify'))
    progress('starting_isolated_php_service')
    install_runtime()
    progress('activating_https_site')
    activate_site(config.get('repair_ip_renewal', False))
    progress('creating_initial_backup')
    backup_service()
    value = json.loads(PRIVATE.read_text())
    if 'admin_password' in value:
        from nacl.public import PublicKey, SealedBox
        credentials = {'username':'掇花者', 'password':value['admin_password'],
                       'forum':'https://' + DOMAIN + '/', 'admin':'https://' + DOMAIN + '/admin.php'}
        sealed = SealedBox(PublicKey(base64.b64decode(config['recipient_public_key']))).encrypt(json.dumps(credentials, ensure_ascii=False).encode())
        envelope = {'credentials_sealed':base64.b64encode(sealed).decode()}
        atomic_json(STATE / 'administrator-envelope.json', envelope)
        value.pop('admin_password')
        atomic_json(PRIVATE, value)
    else:
        envelope = json.loads((STATE / 'administrator-envelope.json').read_text())
    report = {'ok':True,'stage':'complete','site':'https://' + DOMAIN + '/',
              'upstream_commit':config['commit'],'version':config['version'],
              'native_discuz_verified':verification, 'backup_timer':True,
              'existing_applications_healthy':True, **envelope}
    atomic_json(REPORT, report)
    emit(**report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup', action='store_true')
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run in the one-time administrator SSH session')
    try:
        if arguments.backup:
            backups()
        else:
            with (STATE / 'deploy.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                main()
    except Exception as error:
        emit(ok=False, stage='error', message=str(error))
        raise SystemExit(1)
