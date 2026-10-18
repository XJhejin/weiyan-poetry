"""Address upgrades must preserve custom servers and unrelated preferences."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '客户端'))
import main


class DefaultServerTests(unittest.TestCase):
    def check_profile(self, saved, expected, should_write=False):
        with tempfile.TemporaryDirectory(prefix='weiyan-config-') as folder:
            profile = Path(folder)
            config = profile / 'config.json'
            record = profile / 'encrypted-history.fixture'
            record.write_bytes(b'unchanged encrypted data')
            if saved is not None:
                config.write_text(json.dumps(saved), encoding='utf-8')
            before = config.read_bytes() if config.exists() else None
            initial = {'server_url': main.DEFAULT_SERVER,
                       'default_server_revision': main.DEFAULT_SERVER_REVISION}
            with patch.object(main, 'PROFILE', profile), patch.object(main, 'CONFIG_FILE', config), patch.object(main, 'CONFIG', initial):
                main.load_config()
                self.assertEqual(main.CONFIG['server_url'], expected)
                self.assertEqual(record.read_bytes(), b'unchanged encrypted data')
                if should_write:
                    persisted = json.loads(config.read_text(encoding='utf-8'))
                    self.assertEqual(persisted['server_url'], expected)
                    self.assertFalse(persisted['native_notifications'])
                    main.load_config()
                    self.assertEqual(main.CONFIG['server_url'], expected)
                else:
                    self.assertEqual(config.read_bytes() if config.exists() else None, before)

    def test_new_profile_uses_current_public_address(self):
        self.assertEqual(main.DEFAULT_SERVER, 'https://156.225.28.175:23074')
        self.check_profile(None, main.DEFAULT_SERVER)

    def test_previous_official_default_migrates_without_losing_preferences(self):
        self.check_profile({'server_url': main.LEGACY_DEFAULT_SERVER, 'native_notifications': False}, main.DEFAULT_SERVER, True)

    def test_saved_custom_servers_are_preserved(self):
        for address in ('http://127.0.0.1:23074', 'https://chat.example.test', 'http://192.168.1.20:23074', 'https://113.219.237.106:23074'):
            with self.subTest(address=address):
                self.check_profile({'server_url': address, 'native_notifications': False}, address)

    def test_explicit_old_address_after_migration_is_preserved(self):
        self.check_profile({'server_url': main.LEGACY_DEFAULT_SERVER, 'default_server_revision': 1}, main.LEGACY_DEFAULT_SERVER)


if __name__ == '__main__':
    unittest.main()
