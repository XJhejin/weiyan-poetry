"""Release integrity gates for the public download directory."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nacl.signing import SigningKey
import build_site
from update import MANIFEST_DOMAIN, canonical, verify_manifest


class WebsiteTests(unittest.TestCase):
    def test_only_verified_complete_releases_become_public(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);release = root / 'release';release.mkdir()
            key = SigningKey.generate()
            public = base64.b64encode(bytes(key.verify_key)).decode()
            manifest = {'format': 1, 'repository': 'XJhejin/weiyan-poetry', 'version': '2.2.1', 'assets': {}}
            for kind, pattern in build_site.ASSET_NAMES.items():
                name = pattern.format(version='2.2.1');raw = ('fixture:' + kind).encode()
                (release / name).write_bytes(raw)
                manifest['assets'][kind] = {'name': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            def sign():
                body = {k:v for k,v in manifest.items() if k != 'signature'}
                manifest['signature'] = base64.b64encode(key.sign(MANIFEST_DOMAIN + canonical(body)).signature).decode()
                (release / 'weiyan-update.json').write_text(json.dumps(manifest), 'utf-8')
            sign()
            (release / 'not-public.txt').write_text('must not be copied')
            with patch.object(build_site, 'verify_manifest', side_effect=lambda m:verify_manifest(m, public)):
                output = build_site.build(release, root / 'good', '2.2.1')
                self.assertEqual(len(list((output / 'downloads').iterdir())), 7)
                self.assertFalse((output / 'downloads/not-public.txt').exists())
                self.assertNotIn('{{VERSION}}', (output / 'index.html').read_text('utf-8'))
                self.assertIn('/downloads/weiyan-client-2.2.1-android.apk', (output / 'index.html').read_text('utf-8'))
                with self.assertRaises(ValueError): build_site.build(release, root / 'wrong-version', '2.2.2')
                manifest['version'] = '2.2.2'
                (release / 'weiyan-update.json').write_text(json.dumps(manifest), 'utf-8')
                with self.assertRaises(ValueError): build_site.build(release, root / 'bad-signature', '2.2.2')
                self.assertFalse((root / 'bad-signature').exists())
                manifest['version'] = '2.2.1';sign()
                (release / manifest['assets']['client_android']['name']).write_bytes(b'corrupted')
                with self.assertRaises(ValueError): build_site.build(release, root / 'corrupted', '2.2.1')
                self.assertFalse((root / 'corrupted').exists())
                manifest['assets']['client']['name'] = '../../private';sign()
                with self.assertRaises(ValueError): build_site.build(release, root / 'unsafe', '2.2.1')


if __name__ == '__main__': unittest.main(verbosity=2)
