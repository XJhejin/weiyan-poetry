"""Regression checks for standalone Windows updater dependencies and startup."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '客户端'))
import update


class UpdateRuntimeTests(unittest.TestCase):
    def test_self_test_rejects_failed_missing_or_mismatched_runtime(self):
        for outcome in ('missing-cffi', 'missing-report', 'wrong-version', 'not-frozen', 'valid'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder:
                folder = Path(folder)
                source, stage = folder / 'source', folder / 'stage'
                source.mkdir()
                stage.mkdir()
                (source / 'update_helper.exe').write_bytes(b'fixture helper')

                def run(command, **kwargs):
                    self.assertEqual(kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
                    self.assertEqual(Path(kwargs['cwd']), stage)
                    self.assertEqual(Path(command[0]).read_bytes(), b'fixture helper')
                    if outcome == 'missing-cffi':
                        raise subprocess.CalledProcessError(1, command)
                    if outcome != 'missing-report':
                        report = {'status': 'ok', 'protocol': 1, 'version': update.VERSION,
                                  'frozen': True, 'signature_runtime': True}
                        if outcome == 'wrong-version':
                            report['version'] = '0.0.1'
                        if outcome == 'not-frozen':
                            report['frozen'] = False
                        Path(command[2]).write_text(json.dumps(report), encoding='utf-8')

                with patch.object(update, '__file__', str(source / 'update.py')), \
                        patch.object(update.subprocess, 'run', side_effect=run):
                    if outcome == 'valid':
                        self.assertEqual(update.prepare_windows_helper(stage), stage / 'install.exe')
                    else:
                        with self.assertRaisesRegex(ValueError, '原程序保持运行'):
                            update.prepare_windows_helper(stage)

    def test_failed_helper_keeps_application_running(self):
        with tempfile.TemporaryDirectory() as folder:
            package = io.BytesIO()
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr('客户端/未言诗集.exe', b'new executable')
                archive.writestr('客户端/_internal/runtime.txt', b'runtime')
            raw = package.getvalue()
            import hashlib
            updater = update.Updater('client', folder)
            updater.release = {'artifact': {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
                               'asset': {'url': 'https://api.github.com/repos/' + update.REPOSITORY + '/releases/assets/123'}}
            with patch.object(update.urllib.request, 'urlopen', return_value=io.BytesIO(raw)), \
                    patch.object(update, 'prepare_windows_helper', side_effect=ValueError('runtime missing')), \
                    patch.object(update.os, '_exit') as exit_app, \
                    patch.object(update.subprocess, 'Popen') as launch:
                updater._download()
            exit_app.assert_not_called()
            launch.assert_not_called()
            self.assertEqual(updater.progress['stage'], 'error')
            self.assertEqual(updater.progress['message'], 'runtime missing')

    @unittest.skipUnless(os.environ.get('WEIYAN_TEST_BINARIES'), 'requires actual Windows packages')
    def test_real_frozen_client_and_server_launch_copied_helper(self):
        for name, entry in (('未言诗集', '未言诗集.exe'), ('未言诗集服务端', '未言诗集服务端.exe')):
            with self.subTest(application=name), tempfile.TemporaryDirectory(prefix='weiyan-update-check-') as folder:
                folder = Path(folder)
                report = folder / 'report.json'
                env = update.windows_runtime_environment()
                env.pop('PYTHONPATH', None)
                env.pop('PYTHONHOME', None)
                env['WEIYAN_CLIENT_DATA'] = str(folder / 'client-profile')
                env['WEIYAN_DATA_DIR'] = str(folder / 'server-data')
                process = subprocess.run([str(ROOT / 'build-output' / name / entry),
                                          '--check-update-runtime', str(report)],
                                         cwd=folder, env=env, timeout=75, check=True,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(process.returncode, 0)
                value = json.loads(report.read_text('utf-8'))
                self.assertEqual(value, {'status': 'ok', 'protocol': 1, 'version': update.VERSION,
                                         'frozen': True, 'signature_runtime': True})
                self.assertFalse((folder / 'client-profile').exists())
                self.assertFalse((folder / 'server-data').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
