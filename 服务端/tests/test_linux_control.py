"""Restricted RPC, source archive boundaries, and schema rollback on failed updates."""
import importlib.util
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
spec = importlib.util.spec_from_file_location('gateway', SERVER / 'deploy/linux/control_gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
spec2 = importlib.util.spec_from_file_location('linux_bundle', SERVER / 'tools/build_linux_bundle.py')
builder = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(builder)
import admin


@contextmanager
def connect(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


class ArchiveTests(unittest.TestCase):
    def test_real_bundle_has_exact_allowed_sources_and_no_user_data(self):
        self.assertEqual(set(builder.FILES), gateway.SOURCE_FILES)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = builder.build(root)
            gateway.unpack(archive, root / 'unpacked', builder.VERSION)
            self.assertTrue((root / 'unpacked/admin_ui/index.html').is_file())
            self.assertFalse((root / 'unpacked/data').exists())

    def test_traversal_links_duplicates_and_missing_files_rejected(self):
        for kind in ('traversal', 'symlink', 'duplicate', 'missing', 'extra'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / 'bad.tar.gz'
                with tarfile.open(archive, 'w:gz') as bundle:
                    names = ['server.py'] if kind in ('missing', 'duplicate', 'symlink') else ['../data/secret' if kind == 'traversal' else 'data/secret']
                    if kind == 'duplicate':
                        names *= 2
                    for name in names:
                        member = tarfile.TarInfo('weiyan-server-9.9.9/' + name)
                        if kind == 'symlink':
                            member.type = tarfile.SYMTYPE
                            member.linkname = '/etc/passwd'
                        else:
                            member.size = 1
                        bundle.addfile(member, io.BytesIO(b'x') if member.isfile() else None)
                with self.assertRaises(ValueError):
                    gateway.unpack(archive, root / 'out', '9.9.9')


class PermissionsTests(unittest.TestCase):
    def test_rpc_has_no_shell_sql_arbitrary_paths_or_management_token(self):
        with patch.object(gateway, 'run') as command, patch.object(gateway, 'admin_request') as request:
            for operation in ('shell', 'exec', 'sql', 'read', '../etc/passwd', None):
                with self.assertRaises(ValueError):
                    gateway.dispatch({'operation': operation})
            with self.assertRaises(ValueError):
                gateway.dispatch({'operation': 'state', 'command': 'id'})
            for action in ('delete_database', 'shell', 'update'):
                with self.assertRaises(ValueError):
                    gateway.dispatch({'operation': 'action', 'payload': {'action': action}})
            command.assert_not_called()
            request.assert_not_called()

    def test_start_always_uses_fixed_loopback_backend(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gateway, 'STORE', Path(directory)), \
                patch.object(gateway, 'update_lock'), patch.object(gateway, 'admin_request', return_value={'ok': True}) as request:
            gateway.dispatch({'operation': 'action', 'payload': {'action': 'start', 'host': '0.0.0.0', 'port': 22}})
            request.assert_called_once_with('/manage/action', {'action': 'start', 'host': '127.0.0.1', 'port': 23174})

    def test_access_capability_rotation_does_not_delete_new_process_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'admin-url'
            admin.write_access_file(path, 'first-secret')
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            admin.write_access_file(path, 'second-secret')
            admin.remove_access_file(path, 'first-secret')
            self.assertEqual(path.read_text(), 'second-secret')
            admin.remove_access_file(path, 'second-secret')
            self.assertFalse(path.exists())


class ActivationTests(unittest.TestCase):
    def test_old_systemd_can_launch_update_and_duplicate_request_does_not_spawn_again(self):
        launches = []
        def old_systemd(args, **kwargs):
            if args[0] == 'systemd-run':
                if any(arg.startswith('--property=UMask') for arg in args):
                    raise subprocess.CalledProcessError(1, args, stderr='Unknown assignment UMask=0077')
                launches.append(args)
            return Mock(returncode=0)
        with tempfile.TemporaryDirectory() as directory, patch.object(gateway, 'STORE', Path(directory)), \
                patch.object(gateway, 'update_lock'), patch.object(gateway, 'run', side_effect=old_systemd), \
                patch.object(gateway, 'admin_request') as admin:
            self.assertTrue(gateway.start_update()['started'])
            self.assertEqual(gateway.update_status()['stage'], 'queued')
            self.assertTrue(gateway.start_update()['started'])
            self.assertEqual(len(launches), 1)
            admin.assert_not_called()

    def test_launch_failure_returns_actionable_error_without_stopping_chat(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gateway, 'STORE', Path(directory)), \
                patch.object(gateway, 'update_lock'), \
                patch.object(gateway, 'run', side_effect=OSError('systemd-run unavailable')) as command, \
                patch.object(gateway, 'admin_request') as admin:
            with self.assertRaisesRegex(ValueError, '无法启动远程更新任务'):
                gateway.start_update()
            self.assertEqual(gateway.update_status()['stage'], 'error')
            self.assertEqual(command.call_count, 1)
            admin.assert_not_called()

    def test_healthy_switch_and_failed_schema_migration_restore_database(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base, data, store = root / 'base', root / 'data', root / 'control'
                for path in (base / 'current', data, store, root / 'job', base / 'candidate'):
                    path.mkdir(parents=True, exist_ok=True)
                db_path = data / 'weiyan.sqlite3'
                with connect(db_path) as db:
                    db.execute('CREATE TABLE accounts (name TEXT)')
                    db.execute("INSERT INTO accounts VALUES ('existing-account')")
                    db.execute('CREATE TABLE relay (ciphertext BLOB)')
                    db.execute('INSERT INTO relay VALUES (?)', (b'encrypted-queued-packet',))
                def health(expected):
                    if expected == '9.9.9':
                        with connect(db_path) as db:
                            db.execute('ALTER TABLE accounts ADD COLUMN new_schema TEXT')
                        if fail:
                            raise ValueError('new version failed')
                with patch.multiple(gateway, BASE=base, DATA=data, STORE=store), \
                        patch.object(gateway, 'current_version', return_value='2.1.3'), \
                        patch.object(gateway, 'admin_request', return_value={'running': True}), \
                        patch.object(gateway, 'service_state', return_value={'ActiveState': 'active'}), \
                        patch.object(gateway, 'run', return_value=Mock(returncode=0)) as command, \
                        patch.object(gateway, 'swap_current') as switch, \
                        patch.object(gateway, 'wait_healthy', side_effect=health), \
                        patch.object(gateway.os, 'chown', create=True):
                    if fail:
                        with self.assertRaisesRegex(ValueError, 'failed'):
                            gateway.activate(base / 'candidate', '9.9.9', root / 'job')
                        self.assertEqual(switch.call_count, 2)
                    else:
                        gateway.activate(base / 'candidate', '9.9.9', root / 'job')
                        self.assertEqual(switch.call_count, 1)
                    commands = [call.args[0] for call in command.call_args_list]
                    self.assertEqual(commands[0], ['systemctl', 'stop', gateway.PROXY])
                    self.assertEqual(commands[-1], ['systemctl', 'start', gateway.PROXY])
                with connect(db_path) as db:
                    self.assertEqual(db.execute('SELECT name FROM accounts').fetchone()[0], 'existing-account')
                    self.assertEqual(db.execute('SELECT ciphertext FROM relay').fetchone()[0], b'encrypted-queued-packet')
                    self.assertEqual(len(db.execute('PRAGMA table_info(accounts)').fetchall()), 1 if fail else 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
