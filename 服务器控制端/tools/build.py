"""Build the independent controller without embedding any server credentials."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def build():
    source = ROOT / '服务器控制端'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
                    '--name', '未言诗集服务器控制端', '--distpath', str(ROOT / 'build-output'),
                    '--specpath', str(ROOT / '.build'), '--workpath', str(ROOT / '.build/control'),
                    '--icon', str(source / 'ui/logo.ico'), '--add-data', str(source / 'ui') + ';ui',
                    '--collect-all', 'webview', str(source / 'main.py')], cwd=ROOT, check=True)
    notices=(ROOT / '客户端/第三方许可.txt').read_text('utf-8')
    (source / '第三方许可.txt').write_text('\n'.join(line.rstrip() for line in notices.splitlines()).rstrip()+'\n',encoding='utf-8')


if __name__ == '__main__':
    build()
