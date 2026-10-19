"""Build the independent controller without embedding any server credentials."""
from pathlib import Path
from importlib import metadata
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def build():
    sys.path.insert(0, str(ROOT / '客户端/tools'))
    from brand_assets import build_assets
    build_assets()
    source = ROOT / '服务器控制端'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
                    '--name', '未言诗集服务器控制端', '--distpath', str(ROOT / 'build-output'),
                    '--specpath', str(ROOT / '.build'), '--workpath', str(ROOT / '.build/control'),
                    '--icon', str(source / 'ui/logo.ico'), '--add-data', str(source / 'ui') + ';ui',
                    '--collect-all', 'webview', '--collect-all', 'paramiko',
                    str(source / 'main.py')], cwd=ROOT, check=True)
    notices=(ROOT / '客户端/第三方许可.txt').read_text('utf-8')
    for name in ('paramiko', 'cryptography', 'bcrypt', 'invoke'):
        distribution = metadata.distribution(name)
        notices += '\n' + '=' * 60 + '\n' + name + ' ' + distribution.version + '\n\n'
        for file in distribution.files or []:
            if any(part.lower().startswith(('license', 'copying', 'copyright')) for part in file.parts):
                path = distribution.locate_file(file)
                if path.is_file():
                    notices += str(file) + '\n' + path.read_text('utf-8', errors='replace') + '\n'
    (source / '第三方许可.txt').write_text('\n'.join(line.rstrip() for line in notices.splitlines()).rstrip()+'\n',encoding='utf-8')


if __name__ == '__main__':
    build()
