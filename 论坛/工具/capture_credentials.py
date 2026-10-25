"""Save the sealed initial forum credentials with current-user Windows DPAPI."""
from pathlib import Path
import argparse
import base64
import json
import os

from nacl.public import PrivateKey, SealedBox
from secure_storage import private_directory, private_file, protect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-directory', type=Path,
                        default=Path(__file__).resolve().parents[1] / '.build/deploy')
    args = parser.parse_args()
    reports = []
    log = args.session_directory / 'forum-session-output.txt'
    for line in log.read_text('utf-8-sig', errors='replace').splitlines():
        try:
            value = json.loads(line)
            if value.get('stage') == 'complete' and value.get('credentials_sealed'):
                reports.append(value)
        except ValueError:
            continue
    if not reports:
        raise SystemExit('No completed deployment found; leave the SSH session open.')
    profile = private_directory(Path(os.environ['LOCALAPPDATA']) / 'WeiyanForumDeploy')
    key = PrivateKey(protect((profile / 'recipient-key.dpapi').read_bytes(), decrypt=True))
    plain = SealedBox(key).decrypt(base64.b64decode(reports[-1]['credentials_sealed'], validate=True))
    credentials = json.loads(plain)
    if credentials.get('username') != '掇花者' or len(credentials.get('password', '')) < 20:
        raise ValueError('Invalid initial administrator credentials')
    target = profile / 'credentials.dpapi'
    target.write_bytes(protect(plain))
    private_file(target)
    print('Administrator credentials saved with Windows account encryption.')


if __name__ == '__main__':
    main()
