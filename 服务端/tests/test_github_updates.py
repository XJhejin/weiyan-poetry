"""Bundled trust roots, signed fallback, and transport integrity regressions."""
import base64
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch
from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update


class GitHubUpdates(unittest.TestCase):
    def setUp(self):
        self.key = SigningKey.generate()
        self.pub = base64.b64encode(bytes(self.key.verify_key)).decode()
        artifact = {'name': 'weiyan-server-9.0.0-macos-arm64.dmg', 'bytes': 123, 'sha256': 'a' * 64}
        self.manifest = {'format': 1, 'repository': update.REPOSITORY, 'version': '9.0.0', 'assets': {'server_macos_arm64': artifact}}
        self.manifest['signature'] = base64.b64encode(self.key.sign(update.MANIFEST_DOMAIN + update.canonical(self.manifest)).signature).decode()
        self.verify = update.verify_manifest

    def test_bundled_roots_work_without_build_machine_certificates(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': 'missing-ca.pem', 'SSL_CERT_DIR': 'missing-ca-directory'}):
            context = update.github_tls_context()
        self.assertGreater(context.cert_store_stats()['x509_ca'], 50)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def check(self, fetch):
        updater = update.Updater('server', tempfile.gettempdir())
        with patch.object(update.sys, 'platform', 'darwin'), patch.object(updater, 'fetch', side_effect=fetch), patch.object(update, 'verify_manifest', lambda data: self.verify(data, self.pub)):
            result = updater.check()
        return updater, result

    def test_api_outage_uses_signed_release_fallback(self):
        calls = []
        def fetch(url, *args):
            calls.append(url)
            if 'api.github.com' in url:
                raise ValueError('API unavailable')
            return json.dumps(self.manifest).encode()
        updater, result = self.check(fetch)
        self.assertTrue(result['available'])
        self.assertEqual(result['latest'], '9.0.0')
        self.assertTrue(updater.release['asset']['url'].startswith('https://github.com/' + update.REPOSITORY + '/releases/download/v9.0.0/'))
        self.assertEqual(len(calls), 2)

    def test_healthy_api_still_matches_release_tag_and_size(self):
        release = {'tag_name': 'v9.0.0', 'assets': [{'name': 'weiyan-update.json', 'url': 'https://api.github.com/manifest'}, {'name': self.manifest['assets']['server_macos_arm64']['name'], 'size': 123, 'url': 'https://api.github.com/asset'}]}
        self.assertTrue(self.check(lambda url, *args: json.dumps(self.manifest if url.endswith('manifest') else release).encode())[1]['available'])
        release['tag_name'] = 'v8.0.0'
        with self.assertRaisesRegex(ValueError, '标签'):
            self.check(lambda url, *args: json.dumps(self.manifest if url.endswith('manifest') else release).encode())

    def test_fallback_never_accepts_tampered_manifest(self):
        self.manifest['assets']['server_macos_arm64']['sha256'] = 'b' * 64
        def fetch(url, *args):
            if 'api.github.com' in url:
                raise ValueError('API unavailable')
            return json.dumps(self.manifest).encode()
        with self.assertRaises(ValueError):
            self.check(fetch)

    def test_certificate_failure_has_actionable_message(self):
        from urllib.error import URLError
        with patch.object(update.urllib.request, 'urlopen', side_effect=URLError(ssl.SSLCertVerificationError('bad certificate'))):
            with self.assertRaisesRegex(ValueError, '证书'):
                update.Updater('server', '.').fetch('https://api.github.com/test')


if __name__ == '__main__':
    unittest.main(verbosity=2)
