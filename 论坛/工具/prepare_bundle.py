"""Prepare a pinned deployment; keep archives and administrator credentials outside Git."""
from pathlib import Path
import argparse
import base64
import hashlib
import io
import json
import os
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

FORUM = Path(__file__).resolve().parents[1]
from secure_storage import protect, private_directory, private_file
from nacl.public import PrivateKey


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-directory', type=Path, default=FORUM / '.build/deploy')
    parser.add_argument('--diagnose', action='store_true')
    parser.add_argument('--repair-ip-renewal', action='store_true')
    args = parser.parse_args()
    BUILD = args.session_directory.resolve()
    BUILD.mkdir(parents=True, exist_ok=True)
    config = json.loads((FORUM / 'upstream.json').read_text('utf-8'))
    archive = BUILD / ('DiscuzX-' + config['commit'] + '.tar.gz')
    if not archive.is_file():
        pending = archive.with_suffix('.partial')
        with urllib.request.urlopen('https://codeload.github.com/DiscuzTeam/DiscuzX/tar.gz/' + config['commit'], timeout=60) as source, pending.open('wb') as target:
            while chunk := source.read(262144):
                target.write(chunk)
        with tarfile.open(pending, 'r:gz') as source:
            if not any(item.name.endswith('/upload/forum.php') for item in source):
                raise ValueError('Incomplete official source archive')
        os.replace(pending, archive)
    with tarfile.open(archive, 'r:gz') as source:
        if not any(item.name.endswith('/upload/forum.php') for item in source):
            raise ValueError('Invalid cached source archive; download it again')
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if config['source_archive_sha256'] not in ('PENDING', digest):
        raise ValueError('Pinned upstream source changed')
    config['source_archive_sha256'] = digest
    (FORUM / 'upstream.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    profile = private_directory(Path(os.environ['LOCALAPPDATA']) / 'WeiyanForumDeploy')
    recipient = profile / 'recipient-key.dpapi'
    if not recipient.is_file():
        recipient.write_bytes(protect(bytes(PrivateKey.generate())))
        private_file(recipient)
    key = PrivateKey(protect(recipient.read_bytes(), decrypt=True))
    config['recipient_public_key'] = base64.b64encode(bytes(key.public_key)).decode()
    config['operation'] = 'diagnose' if args.diagnose else 'deploy'
    config['repair_ip_renewal'] = args.repair_ip_renewal
    logo = (FORUM / '主题/logo.svg').read_text('utf-8')
    inner = ''.join(ET.tostring(child, encoding='unicode') for child in ET.fromstring(logo) if child.tag.endswith('path'))
    inner = inner.replace('ns0:', '').replace(':ns0', '')
    wordmark = '<svg xmlns="http://www.w3.org/2000/svg" width="224" height="64" viewBox="0 0 224 64"><title>未言诗集 · HushLeaf 社区</title><g transform="translate(0 1) scale(.65)">' + inner + '</g><text x="66" y="29" fill="#493949" font-family="Microsoft YaHei, PingFang SC, sans-serif" font-size="25" font-weight="600" letter-spacing="2">未言诗集</text><text x="68" y="48" fill="#ad87a0" font-family="Segoe UI, sans-serif" font-size="8" letter-spacing="2.6">HUSHLEAF COMMUNITY</text></svg>\n'
    (FORUM / '主题/wordmark.svg').write_text(wordmark, 'utf-8')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name in ('deploy_forum.py', 'configure_forum.php'):
            bundle.writestr(name, (FORUM / '工具' / name).read_bytes())
        for name in ('hushleaf.css', 'mobile.css', 'logo.svg', 'wordmark.svg', 'theme-header.htm', 'mobile-signature.htm', 'LICENSE-notice.md'):
            bundle.writestr(name, (FORUM / '主题' / name).read_bytes())
        for path in sorted((FORUM / '插件/hushleaf_media').iterdir()):
            if path.suffix in ('.php', '.js', '.css'):
                bundle.writestr(path.name, path.read_bytes())
        bundle.writestr('forum-config.json', json.dumps(config))
    raw = buffer.getvalue()
    (BUILD / 'forum-bundle.zip').write_bytes(raw)
    requests = BUILD / 'forum-requests'
    requests.mkdir(exist_ok=True)
    identifier = 'deploy-' + str(__import__('time').time_ns())
    request = {'id': identifier, 'operation': 'deploy', 'payload':
               {'source': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()}}
    temporary = requests / (identifier + '.tmp')
    temporary.write_text(json.dumps(request), 'utf-8')
    os.replace(temporary, requests / (identifier + '.json'))
    print(json.dumps({'queued': True, 'id': identifier, 'upstream_sha256': digest, 'bundle_bytes': len(raw)}))


if __name__ == '__main__':
    main()
