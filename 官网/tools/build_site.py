"""Build an entirely self-hosted website from verified release assets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE.parent / '客户端'))
# Installed Linux control runtime also provides the pinned release verifier.
if Path('/opt/weiyan/control/update.py').is_file():
    sys.path.insert(0, '/opt/weiyan/control')
from update import verify_manifest

ASSET_NAMES = {
    'client': 'weiyan-client-{version}-windows-x64.zip',
    'server': 'weiyan-server-{version}-windows-x64.zip',
    'client_windows_msi': 'weiyan-client-{version}-windows-x64.msi',
    'control_windows_msi': 'weiyan-control-{version}-windows-x64.msi',
    'client_android': 'weiyan-client-{version}-android.apk',
    'server_linux_source': 'weiyan-server-{version}-linux-source.tar.gz',
}


def check_assets(manifest, expected_version):
    if manifest['version'] != expected_version or set(manifest['assets']) != set(ASSET_NAMES):
        raise ValueError('Unexpected release version or artifact list')
    for kind, pattern in ASSET_NAMES.items():
        item = manifest['assets'][kind]
        if item['name'] != pattern.format(version=expected_version) or not isinstance(item['bytes'], int) or item['bytes'] <= 0:
            raise ValueError('Invalid signed artifact name or size')


def build(release_dir, output, expected_version):
    manifest = json.loads((release_dir / 'weiyan-update.json').read_text('utf-8'))
    verify_manifest(manifest)
    check_assets(manifest, expected_version)
    for artifact in manifest['assets'].values():
        source = release_dir / artifact['name']
        if source.is_symlink() or not source.is_file() or source.stat().st_size != artifact['bytes']:
            raise ValueError('Missing or invalid artifact: ' + artifact['name'])
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != artifact['sha256']:
                raise ValueError('Artifact SHA-256 mismatch: ' + artifact['name'])
    output.mkdir(parents=True, exist_ok=False)
    for name in ('index.html', 'styles.css', 'app.js', 'privacy.html', 'logo.svg'):
        text = (SOURCE / name).read_text('utf-8').replace('{{VERSION}}', expected_version)
        (output / name).write_text(text, 'utf-8')
    (output / 'release.json').write_text(json.dumps({'version': expected_version, 'assets': manifest['assets']}, indent=2), 'utf-8')
    downloads = output / 'downloads'
    downloads.mkdir()
    for name in ['weiyan-update.json'] + [a['name'] for a in manifest['assets'].values()]:
        shutil.copyfile(release_dir / name, downloads / name)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    print(build(args.release_dir.resolve(), args.output.resolve(), args.version))
