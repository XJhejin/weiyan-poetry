"""One-shot root deployment; never adds SSH authorizations or exposes chat data."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from build_site import build, check_assets
from update import github_tls_context, verify_manifest

BASE = Path('/opt/weiyan/website')
CONFIG = Path('/etc/weiyan/Caddyfile')
CADDY = '/opt/weiyan/caddy/caddy'
DOMAIN = 'www.hushleaf.top'
REPOSITORY = 'XJhejin/weiyan-poetry'


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def download(url, target, expected=None):
    digest, size = hashlib.sha256(), 0
    temporary = target.with_suffix(target.suffix + '.partial')
    limit = expected['bytes'] if expected else 262144
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'HushLeaf-Website-Deploy'})
        with urllib.request.urlopen(request, timeout=45, context=github_tls_context()) as source, temporary.open('wb') as output:
            while chunk := source.read(131072):
                size += len(chunk)
                if size > limit:
                    raise ValueError('Download exceeds signed size')
                output.write(chunk);digest.update(chunk)
        if expected and (size != expected['bytes'] or digest.hexdigest() != expected['sha256']):
            raise ValueError('Download failed SHA-256 validation')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def site_block():
    return '''# BEGIN HushLeaf website
http://www.hushleaf.top {
    redir https://www.hushleaf.top{uri} 308
}
https://www.hushleaf.top {
    root * /opt/weiyan/website/current
    encode zstd gzip
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
        X-Frame-Options DENY
        Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        Cache-Control no-cache
    }
    file_server
}
# END HushLeaf website
'''


def deploy(expected_version):
    if os.geteuid() != 0:
        raise ValueError('Run through the one-time administrator SSH session')
    if not re.fullmatch(r'\d+\.\d+\.\d+', expected_version):
        raise ValueError('Invalid pinned version')
    os.umask(0o022)
    BASE.mkdir(parents=True, exist_ok=True)
    with (BASE / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assets = BASE / 'releases' / expected_version
        assets.mkdir(parents=True, exist_ok=True)
        prefix = f'https://github.com/{REPOSITORY}/releases/download/v{expected_version}/'
        download(prefix + 'weiyan-update.json', assets / 'weiyan-update.json')
        manifest = json.loads((assets / 'weiyan-update.json').read_text('utf-8'))
        verify_manifest(manifest)
        check_assets(manifest, expected_version)
        if manifest['repository'] != REPOSITORY or manifest['version'] != expected_version:
            raise ValueError('Unexpected signed release')
        for item in manifest['assets'].values():
            target = assets / item['name']
            valid = False
            if target.is_file() and target.stat().st_size == item['bytes']:
                with target.open('rb') as stream:
                    valid = hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256']
            if not valid:
                print('Downloading and verifying ' + item['name'], flush=True)
                download(prefix + item['name'], target, item)
        stamp = expected_version + '-' + str(time.time_ns())
        candidate = build(assets, BASE / 'versions' / stamp / 'public', expected_version)
        current = BASE / 'current'
        previous = current.resolve() if current.is_symlink() else None
        old_config = CONFIG.read_bytes()
        backup = BASE / ('Caddyfile.before-' + stamp)
        backup.write_bytes(old_config)
        config = old_config.decode('utf-8')
        pattern = r'# BEGIN HushLeaf website\n.*?# END HushLeaf website\n?'
        config = re.sub(pattern, '', config, flags=re.S).rstrip() + '\n\n' + site_block()
        trial = BASE / 'Caddyfile.candidate'
        trial.write_text(config, 'utf-8')
        # Validate using the same non-secret public address the installed service uses.
        env = os.environ.copy();env['WEIYAN_PUBLIC_IP'] = '156.225.28.175'
        subprocess.run([CADDY, 'validate', '--config', str(trial), '--adapter', 'caddyfile'], env=env, check=True)
        firewall = subprocess.run(['systemctl', 'is-active', 'firewalld'], capture_output=True).returncode == 0
        if firewall:
            run('firewall-cmd', '--add-port=443/tcp')
            run('firewall-cmd', '--permanent', '--add-port=443/tcp')
        pending = BASE / 'current.new'
        pending.unlink(missing_ok=True);pending.symlink_to(candidate, target_is_directory=True)
        os.replace(pending, current)
        try:
            shutil.copyfile(trial, CONFIG)
            run('systemctl', 'restart', 'weiyan-proxy.service')
            healthy = False
            for _ in range(40):
                try:
                    with urllib.request.urlopen('https://' + DOMAIN + '/release.json', timeout=8) as response:
                        healthy = response.status == 200 and json.loads(response.read(262144))['version'] == expected_version
                    if healthy: break
                except Exception:
                    time.sleep(3)
            if not healthy:
                raise ValueError('Website HTTPS verification failed')
            with urllib.request.urlopen('https://156.225.28.175:23074/api/health', timeout=8) as response:
                if not json.loads(response.read(8192)).get('ok'):
                    raise ValueError('Existing chat endpoint failed verification')
        except Exception:
            CONFIG.write_bytes(old_config)
            if previous:
                pending.symlink_to(previous, target_is_directory=True);os.replace(pending, current)
            else:
                current.unlink(missing_ok=True)
            run('systemctl', 'restart', 'weiyan-proxy.service')
            raise
        report = {'ok': True, 'version': expected_version, 'site': 'https://' + DOMAIN,
                  'artifacts': len(manifest['assets']), 'signed_assets_verified': True,
                  'chat_healthy': True, 'previous_site': str(previous) if previous else None,
                  'config_backup': str(backup)}
        (BASE / 'deployment.json').write_text(json.dumps(report, indent=2), 'utf-8')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    deploy(parser.parse_args().version)
