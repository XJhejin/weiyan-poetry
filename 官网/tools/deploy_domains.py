"""Add fixed public domain aliases through a temporary administrator SSH session."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request

PUBLIC_IP = '156.225.28.175'
BASE = Path('/opt/weiyan/domain-aliases')
CONFIG = Path('/etc/weiyan/Caddyfile')
CADDY = '/opt/weiyan/caddy/caddy'
NGINX = '/www/server/nginx/sbin/nginx'
NGINX_CONFIG = Path('/www/server/panel/vhost/nginx/hushleaf-domain-aliases.conf')
BEGIN = '# BEGIN HushLeaf domain aliases'
END = '# END HushLeaf domain aliases'
SITES = (
    ('HushLeaf website', 'www.hushleaf.top',
     ('7me.top', 'www.7me.top', 'weiyan.ink', 'www.weiyan.ink')),
    ('HushLeaf Discuz forum', 'bbs.hushleaf.top',
     ('bbs.7me.top', 'bbs.weiyan.ink')),
    ('Weiyan personal site', 'weiyan.hushleaf.top',
     ('weiyan.7me.top', 'weiyan.weiyan.ink', 'info.weiyan.ink', 'intro.weiyan.ink')),
)
HOSTS = tuple(host for _, _, aliases in SITES for host in aliases)
CHAT_HOSTS = ('7me.top', 'weiyan.ink')


def emit(**value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def managed_pattern(begin=BEGIN, end=END):
    return re.compile(r'^' + re.escape(begin) + r'\r?\n(.*?)^' + re.escape(end) + r'\r?\n?', re.M | re.S)


def owned_hosts(text):
    blocks = list(managed_pattern().finditer(text))
    if len(blocks) > 1:
        raise ValueError('Duplicate managed alias sections')
    headers = re.findall(r'^https://[^\n{]+\{', blocks[0][1], re.M) if blocks else []
    return {host for header in headers for host in re.findall(r'https://([a-z0-9.-]+)', header)}


def build_candidate(text, enabled):
    """Copy the current public handlers, preserving all original site blocks."""
    enabled = set(enabled)
    if not enabled or not enabled.issubset(HOSTS):
        raise ValueError('Unexpected or empty alias list')
    previous = owned_hosts(text)
    if not previous.issubset(HOSTS):
        raise ValueError('Unrecognized aliases in managed section')
    original = managed_pattern().sub('', text).rstrip()
    pieces = [BEGIN]
    for label, source, aliases in SITES:
        names = [host for host in aliases if host in enabled]
        if not names:
            continue
        source_blocks = list(managed_pattern('# BEGIN ' + label, '# END ' + label).finditer(original))
        if len(source_blocks) != 1:
            raise ValueError('Missing or ambiguous source configuration: ' + source)
        block = source_blocks[0][1].strip()
        header = 'https://' + source + ' {'
        if not block.startswith((header + '\n', header + '\r\n')) or not block.endswith('}'):
            raise ValueError('Unrecognized source configuration: ' + source)
        for host in names:
            if re.search(r'(?<![a-z0-9.-])' + re.escape(host) + r'(?![a-z0-9.-])', original):
                raise ValueError('Alias already belongs to an unmanaged configuration: ' + host)
        # The exact same roots, PHP restrictions, size limits and TLS settings apply.
        pieces.append(', '.join('https://' + host for host in names) + ' {' + block[len(header):])
    chat = [host for host in CHAT_HOSTS if host in enabled]
    if chat:
        matches = list(re.finditer(
            r'^https://(?:\{\$WEIYAN_PUBLIC_IP\}|156\.225\.28\.175):23074 \{\r?\n.*?^\}',
            original, re.M | re.S))
        if len(matches) != 1 or 'issuer acme {' not in matches[0][0]:
            raise ValueError('Unrecognized existing chat HTTPS configuration')
        body = matches[0][0].split('\n', 1)[1]
        # Preserve the existing IP certificate policy; hostname aliases use
        # the standard domain certificate policy.
        body = re.sub(r'^\s*profile shortlived\r?\n', '', body, flags=re.M)
        pieces.append(', '.join('https://' + host + ':23074' for host in chat) + ' {\n' + body)
    pieces.append(END)
    return original + '\n\n' + '\n\n'.join(pieces) + '\n'


def nginx_candidate(enabled):
    names = [host for host in HOSTS if host in enabled]
    if not names:
        raise ValueError('Empty HTTP alias list')
    return ('# Managed by HushLeaf domain alias deployment.\nserver {\n'
            '    listen 80;\n    server_name ' + ' '.join(names) + ';\n'
            '    return 301 https://$host$request_uri;\n}\n')


def dns_addresses(host):
    try:
        return sorted({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    except OSError:
        return []


def run(command):
    result = subprocess.run(command, capture_output=True, timeout=120)
    if result.returncode:
        path = BASE / ('maintenance-error-' + str(time.time_ns()) + '.log')
        path.write_bytes(result.stdout + b'\n' + result.stderr)
        path.chmod(0o600)
        raise RuntimeError('Server command failed: ' + Path(command[0]).name)
    return result.stdout


def request(url, *, limit=2 * 1024 * 1024, method='GET', headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=12) as response:
        return response.status, response.read(limit), response.headers, response.url


def probe(host, source):
    if source == 'chat':
        status, body, _, final = request('https://' + host + '/api/health', limit=8192)
        if status != 200 or not json.loads(body).get('ok') or final != 'https://' + host + '/api/health':
            raise ValueError('Chat endpoint is unavailable')
        return {'host': host, 'role': 'chat', 'https': True}
    if source == 'www.hushleaf.top':
        status, body, _, final = request('https://' + host + '/release.json', limit=262144)
        metadata = json.loads(body)
        if status != 200 or not metadata.get('assets') or not final.startswith('https://' + host + '/'):
            raise ValueError('Release metadata is unavailable')
        status, body, _, final = request('https://' + host + '/')
        if status != 200 or '未言诗集'.encode() not in body or not final.startswith('https://' + host + '/'):
            raise ValueError('Website is unavailable')
        for kind in ('client_windows_msi', 'client_android'):
            asset = metadata['assets'][kind]
            name = asset['name']
            if not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
                raise ValueError('Unexpected download filename')
            url = 'https://' + host + '/downloads/' + name
            status, partial, headers, final = request(url, limit=32, headers={'Range': 'bytes=0-15'})
            if (status != 206 or len(partial) != 16 or
                    headers.get('Content-Range') != 'bytes 0-15/' + str(asset['bytes']) or final != url):
                raise ValueError('Download range or signed file size differs')
        return {'host': host, 'role': 'website', 'https': True, 'downloads': True, 'version': metadata['version']}
    if source == 'bbs.hushleaf.top':
        status, body, _, final = request('https://' + host + '/forum.php?mobile=no')
        if status != 200 or '未言诗集'.encode() not in body or not final.startswith('https://' + host + '/'):
            raise ValueError('Forum is unavailable')
        for path in ('/config/config_global.php', '/source/class/class_core.php'):
            try:
                request('https://' + host + path)
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    continue
            raise ValueError('Private forum path is not blocked')
        return {'host': host, 'role': 'forum', 'https': True, 'private_paths_blocked': True}
    status, body, headers, final = request('https://' + host + '/')
    if (status != 200 or '夢の言葉はまだ詩になっていない'.encode() not in body or
            not final.startswith('https://' + host + '/') or not headers.get('Content-Security-Policy')):
        raise ValueError('Personal site is unavailable')
    return {'host': host, 'role': 'personal', 'https': True}


def existing_health():
    for _, source, _ in SITES:
        probe(source, source)
    status, body, _, _ = request('https://' + PUBLIC_IP + ':23074/api/health', limit=8192)
    if status != 200 or not json.loads(body).get('ok'):
        raise RuntimeError('Existing chat endpoint failed verification')


def verify_hosts(enabled, *, wait=0):
    pending = {host: source for _, source, aliases in SITES for host in aliases if host in enabled}
    pending.update({host + ':23074': 'chat' for host in CHAT_HOSTS if host in enabled})
    results = {}
    deadline = time.monotonic() + wait
    errors = {}
    while pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(probe, host, source): host for host, source in pending.items()}
            for future in concurrent.futures.as_completed(futures):
                host = futures[future]
                try:
                    results[host] = future.result()
                except Exception as error:
                    errors[host] = type(error).__name__
        pending = {host: source for host, source in pending.items() if host not in results}
        if pending:
            if time.monotonic() >= deadline:
                raise RuntimeError('Public HTTPS verification failed for: ' + ', '.join(
                    host + ' (' + errors.get(host, 'unknown') + ')' for host in sorted(pending)))
            time.sleep(3)
    order = HOSTS + tuple(host + ':23074' for host in CHAT_HOSTS)
    return [results[host] for host in order if host in results]


def verify():
    current = owned_hosts(CONFIG.read_text('utf-8'))
    if not current or not current.issubset(HOSTS):
        raise ValueError('No recognized domain aliases are configured')
    results = verify_hosts(current)
    existing_health()
    emit(ok=True, domains=results, pending_dns=sorted(set(HOSTS) - current), existing_services_healthy=True)


def _deploy():
    if os.geteuid() != 0:
        raise RuntimeError('An authenticated administrator SSH session is required')
    import fcntl
    os.umask(0o022)
    BASE.mkdir(mode=0o755, parents=True, exist_ok=True)
    BASE.chmod(0o755)
    with (BASE / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old_config = CONFIG.read_bytes()
        old_nginx = NGINX_CONFIG.read_bytes() if NGINX_CONFIG.is_file() else None
        text = old_config.decode('utf-8')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            dns = dict(zip(HOSTS, pool.map(dns_addresses, HOSTS)))
        enabled = {host for host, addresses in dns.items() if addresses == [PUBLIC_IP]} | owned_hosts(text)
        missing = sorted(set(HOSTS) - enabled)
        candidate = build_candidate(text, enabled)
        for path in NGINX_CONFIG.parent.glob('*.conf'):
            if path == NGINX_CONFIG:
                continue
            names = re.findall(r'^\s*server_name\s+([^;]+);', path.read_text('utf-8', errors='replace'), re.M)
            if any(set(row.split()) & enabled for row in names):
                raise RuntimeError('An alias already has an unmanaged HTTP site: ' + path.name)
        emit(progress='正在备份域名配置并验证现有官网、论坛和聊天服务…', pending_dns=missing)
        existing_health()
        stamp = str(time.time_ns())
        backup = BASE / 'backups' / stamp
        backup.mkdir(mode=0o700, parents=True)
        (backup / 'Caddyfile').write_bytes(old_config)
        (backup / 'Caddyfile').chmod(0o600)
        if old_nginx is not None:
            (backup / 'nginx.conf').write_bytes(old_nginx)
            (backup / 'nginx.conf').chmod(0o600)
        trial = BASE / 'Caddyfile.candidate'
        trial.write_text(candidate, 'utf-8')
        trial.chmod(0o644)
        run(['runuser', '-u', 'weiyan-proxy', '--', 'env', 'HOME=/var/lib/weiyan-proxy',
             'WEIYAN_PUBLIC_IP=' + PUBLIC_IP, CADDY, 'validate', '--config', str(trial), '--adapter', 'caddyfile'])
        if CONFIG.read_bytes() != old_config:
            raise RuntimeError('Proxy configuration changed during preflight')
        try:
            NGINX_CONFIG.write_text(nginx_candidate(enabled), 'utf-8')
            NGINX_CONFIG.chmod(0o644)
            run([NGINX, '-t'])
            CONFIG.write_text(candidate, 'utf-8')
            # The installed proxy has its admin listener disabled, so restart is required.
            run(['systemctl', 'restart', 'weiyan-proxy.service'])
            run([NGINX, '-s', 'reload'])
            emit(progress='正在申请新域名 HTTPS 证书并验证网站、下载与论坛访问…')
            results = verify_hosts(enabled, wait=180)
            existing_health()
        except Exception:
            CONFIG.write_bytes(old_config)
            if old_nginx is None:
                NGINX_CONFIG.unlink(missing_ok=True)
            else:
                NGINX_CONFIG.write_bytes(old_nginx)
            run(['systemctl', 'restart', 'weiyan-proxy.service'])
            run([NGINX, '-t'])
            run([NGINX, '-s', 'reload'])
            emit(rollback=True)
            raise
        report = {'ok': True, 'domains': results, 'pending_dns': missing,
                  'shared_existing_data': True, 'existing_services_healthy': True, 'backup': str(backup)}
        (BASE / 'deployment.json').write_text(json.dumps(report, ensure_ascii=False), 'utf-8')
        emit(**report)


def deploy():
    try:
        _deploy()
    except Exception as error:
        emit(ok=False, error_type=type(error).__name__, error=str(error)[:500])
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    verify() if args.verify else deploy()
