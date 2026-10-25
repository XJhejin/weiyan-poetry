"""Deploy only the personal static site through a temporary administrator session."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

DOMAIN = 'weiyan.hushleaf.top'
BASE = Path('/opt/weiyan/personal-site')
CONFIG = Path('/etc/weiyan/Caddyfile')
CADDY = '/opt/weiyan/caddy/caddy'
NGINX = '/www/server/nginx/sbin/nginx'
NGINX_CONFIG = Path('/www/server/panel/vhost/nginx/weiyan.hushleaf.top.conf')
FILES = {'index.html', 'styles.css', 'app.js', 'assets/avatar.webp', 'assets/quill.svg', 'robots.txt', 'sitemap.xml'}
BEGIN = '# BEGIN Weiyan personal site'
END = '# END Weiyan personal site'


def emit(**value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def run(command):
    result = subprocess.run(command, capture_output=True, timeout=90)
    if result.returncode:
        # Diagnostics stay private on the server; never return configurations or secrets.
        destination = BASE / ('maintenance-error-' + str(time.time_ns()) + '.log')
        destination.write_bytes(result.stdout + b'\n' + result.stderr)
        destination.chmod(0o600)
        raise RuntimeError('Server command failed: ' + Path(command[0]).name)
    return result.stdout


def site_block():
    return '''# BEGIN Weiyan personal site
https://weiyan.hushleaf.top {
    tls {
        issuer acme {
            dir https://acme-v02.api.letsencrypt.org/directory
            disable_http_challenge
        }
    }
    root * /opt/weiyan/personal-site/current
    encode zstd gzip
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
        X-Frame-Options DENY
        Content-Security-Policy "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        Cache-Control no-cache
        -Server
    }
    file_server
}
# END Weiyan personal site
'''


def request(url):
    with urllib.request.urlopen(url, timeout=12) as response:
        return response.status, response.read(2 * 1024 * 1024), response.headers


def existing_health():
    checks = [
        ('https://www.hushleaf.top/release.json', b'"version"'),
        ('https://bbs.hushleaf.top/forum.php', '未言诗集'.encode()),
        ('https://156.225.28.175:23074/api/health', b'"ok"'),
    ]
    for url, marker in checks:
        for attempt in range(4):
            try:
                status, body, _ = request(url)
                if status == 200 and marker in body:
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            raise RuntimeError('Existing service did not pass health check: ' + url)


def reload_proxy():
    supported = run(['systemctl', 'show', 'weiyan-proxy.service', '--property=CanReload']).decode().strip()
    run(['systemctl', 'reload' if supported == 'CanReload=yes' else 'restart', 'weiyan-proxy.service'])


def deploy(payload):
    if os.geteuid() != 0:
        raise RuntimeError('An authenticated administrator SSH session is required')
    if set(payload) != FILES:
        raise ValueError('Unexpected static file list')
    os.umask(0o022)
    BASE.mkdir(mode=0o755, parents=True, exist_ok=True)
    BASE.chmod(0o755)
    with (BASE / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing_health()
        stamp = str(time.time_ns())
        versions = BASE / 'versions'
        versions.mkdir(mode=0o755, exist_ok=True)
        versions.chmod(0o755)
        candidate = versions / stamp
        candidate.mkdir(mode=0o755)
        total = 0
        for name, item in payload.items():
            raw = base64.b64decode(item['content'], validate=True)
            total += len(raw)
            if total > 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Invalid file size or digest')
            target = candidate / name
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            target.parent.chmod(0o755)
            target.write_bytes(raw)
            target.chmod(0o644)
        current = BASE / 'current'
        if current.exists() and not current.is_symlink():
            raise RuntimeError('Refusing to replace an unmanaged directory')
        previous = current.readlink() if current.is_symlink() else None
        old_config = CONFIG.read_bytes()
        old_nginx = NGINX_CONFIG.read_bytes() if NGINX_CONFIG.is_file() else None
        text = old_config.decode('utf-8')
        if DOMAIN in text and BEGIN not in text:
            raise RuntimeError('An unmanaged configuration already owns this domain')
        replacement = re.sub(re.escape(BEGIN) + r'\n.*?' + re.escape(END) + r'\n?', '', text, flags=re.S).rstrip() + '\n\n' + site_block()
        backup = BASE / ('backup-' + stamp)
        backup.mkdir(mode=0o700)
        (backup / 'Caddyfile').write_bytes(old_config)
        (backup / 'state.json').write_text(json.dumps({'previous': str(previous) if previous else None}), 'utf-8')
        if old_nginx is not None:
            (backup / 'nginx.conf').write_bytes(old_nginx)
        trial = BASE / 'Caddyfile.candidate'
        trial.write_text(replacement, 'utf-8')
        trial.chmod(0o644)
        emit(progress='正在验证个人站 HTTPS 配置与静态资源权限…')
        run(['runuser', '-u', 'weiyan-proxy', '--', 'env', 'HOME=/var/lib/weiyan-proxy',
             'WEIYAN_PUBLIC_IP=156.225.28.175', CADDY, 'validate', '--config', str(trial), '--adapter', 'caddyfile'])
        for name in FILES:
            run(['runuser', '-u', 'weiyan-proxy', '--', 'test', '-r', str(candidate / name)])
        pending = BASE / 'current.new'
        pending.unlink(missing_ok=True)
        try:
            NGINX_CONFIG.write_text('server {\n    listen 80;\n    server_name ' + DOMAIN +
                ';\n    return 301 https://' + DOMAIN + '$request_uri;\n}\n', 'utf-8')
            NGINX_CONFIG.chmod(0o644)
            run([NGINX, '-t'])
            pending.symlink_to(candidate, target_is_directory=True)
            os.replace(pending, current)
            CONFIG.write_text(replacement, 'utf-8')
            reload_proxy()
            run([NGINX, '-s', 'reload'])
            emit(progress='正在等待新域名证书并核对公网访问…')
            for attempt in range(36):
                try:
                    status, body, headers = request('https://' + DOMAIN + '/')
                    if status == 200 and hashlib.sha256(body).hexdigest() == payload['index.html']['sha256']:
                        break
                except Exception:
                    pass
                time.sleep(3)
            else:
                raise RuntimeError('Personal site HTTPS verification failed')
            for name in ('styles.css', 'app.js', 'assets/avatar.webp', 'assets/quill.svg'):
                status, body, _ = request('https://' + DOMAIN + '/' + name)
                if status != 200 or hashlib.sha256(body).hexdigest() != payload[name]['sha256']:
                    raise RuntimeError('Public static asset differs from source')
            existing_health()
        except Exception:
            CONFIG.write_bytes(old_config)
            if old_nginx is None:
                NGINX_CONFIG.unlink(missing_ok=True)
            else:
                NGINX_CONFIG.write_bytes(old_nginx)
            pending.unlink(missing_ok=True)
            if previous is not None:
                pending.symlink_to(previous, target_is_directory=True)
                os.replace(pending, current)
            else:
                current.unlink(missing_ok=True)
            reload_proxy()
            run([NGINX, '-t'])
            run([NGINX, '-s', 'reload'])
            emit(rollback=True)
            raise
        report = {'ok': True, 'site': 'https://' + DOMAIN + '/', 'files': len(FILES),
                  'https_verified': True, 'public_hashes_verified': True,
                  'existing_services_healthy': True, 'backup': str(backup)}
        (BASE / 'deployment.json').write_text(json.dumps(report), 'utf-8')
        emit(**report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload', type=Path, help='JSON with the fixed static file list, content and SHA-256')
    deploy(json.loads(parser.parse_args().payload.read_text('utf-8')))
