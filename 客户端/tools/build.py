"""Build two independent portable Windows programs. Run with Python 3.12 x64."""
import argparse
from pathlib import Path
from importlib import metadata
import shutil
import subprocess
import sys
from PyInstaller.archive.readers import CArchiveReader
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "客户端" / "ui" / "assets"


def make_icon():
    # An original transparent book-and-petals mark. No enclosing tile or border.
    scale = 8
    image = Image.new("RGBA", (96 * scale, 96 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pink = (206, 129, 158, 255)
    def curve(points, width=3.8):
        draw.line([(round(x * scale), round(y * scale)) for x, y in points], fill=pink, width=round(width * scale), joint="curve")
        radius = width * scale / 2
        for x, y in (points[0], points[-1]):
            x, y = x * scale, y * scale
            draw.ellipse((int(x-radius), int(y-radius), int(x+radius), int(y+radius)), fill=pink)
    curve([(16,31),(24,29),(32,29),(40,31),(48,36),(56,31),(64,29),(72,29),(80,31),(80,73),(72,71),(64,72),(56,74),(48,78),(40,74),(32,72),(24,71),(16,73),(16,31)])
    curve([(48,37),(48,77)])
    curve([(25,44),(30,44),(35,44.5),(40,46)],2.8)
    curve([(25,55),(30,55),(35,55.5),(40,57)],2.8)
    curve([(56,54),(61,52),(66,51.5),(71,52)],2.8)
    curve([(62,36),(62,24)],3.5)
    curve([(62,25),(57,24),(53,21),(52,17),(54,13),(57,13),(60,16),(62,21),(64,16),(67,13),(70,13),(72,17),(71,21),(67,24),(62,25)],3)
    curve([(62,34),(67,33),(70,30),(72,28)],3)
    image = image.resize((256,256), Image.Resampling.LANCZOS)
    image.save(ASSETS / "logo.png")
    image.save(ASSETS / "logo.ico", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


def build(staging_only=False):
    make_icon()
    make_notices()
    staging = ROOT / "build-output"
    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(staging),
              "--specpath", str(ROOT / ".build"), "--icon", str(ASSETS / "logo.ico"),
              "--hidden-import", "simple_websocket", "--hidden-import", "_cffi_backend", "--collect-all", "nacl", '--collect-data', 'certifi']
    subprocess.run(common + ['--workpath', str(ROOT / '.build' / 'updater'), '--name', 'update_helper', '--windowed', '--onefile',
                             '--paths', str(ROOT / '客户端'), str(ROOT / '客户端' / 'tools' / 'update_helper.py')], cwd=ROOT, check=True)
    helper = staging / 'update_helper.exe'
    if not any(name.startswith('_cffi_backend.') for name in CArchiveReader(str(helper)).toc):
        raise RuntimeError('更新助手缺少 _cffi_backend，已阻止生成安装包。')
    subprocess.run(common + ["--workpath", str(ROOT / ".build" / "client"), "--name", "未言诗集", "--windowed", "--onedir",
                            "--add-data", f"{ROOT / '客户端' / 'ui'};ui", '--add-data', f'{helper};.', "--collect-all", "webview",
                            str(ROOT / "客户端" / "main.py")], cwd=ROOT, check=True)
    subprocess.run(common + ["--workpath", str(ROOT / ".build" / "server"), "--name", "未言诗集服务端", "--windowed", "--onedir",
                            '--add-data', f"{ROOT / '服务端' / 'admin_ui'};admin_ui", '--add-data', f'{helper};.', '--collect-all', 'webview',
                            str(ROOT / "服务端" / "server.py")], cwd=ROOT, check=True)
    if not staging_only:
        shutil.copytree(staging / "未言诗集", ROOT / "客户端", dirs_exist_ok=True)
        shutil.copytree(staging / "未言诗集服务端", ROOT / "服务端", dirs_exist_ok=True)
    print("\nBuilt: build-output/未言诗集 and build-output/未言诗集服务端", flush=True)


def make_notices():
    packages = ["pywebview", "pythonnet", "clr_loader", "cffi", "pycparser", "proxy_tools", "bottle", "typing_extensions", "simple-websocket", "wsproto", "h11", 'PyNaCl', 'pystray', 'Pillow', 'certifi']
    shared = "未言诗集 · 第三方组件许可\n\n本项目的界面、Logo 和插画为原创。通信设计参考 HarmonyChat，未复制其代码或素材。\n\n"
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shared += "Python runtime\n" + python_license.read_text(encoding="utf-8") + "\n\n"
    sections = {}
    for name in packages:
        distribution = metadata.distribution(name)
        text = f"{'=' * 60}\n{name} {distribution.version}\n\n"
        for file in distribution.files or []:
            if any(part.lower().startswith(("license", "copying", "copyright")) for part in file.parts):
                path = distribution.locate_file(file)
                if path.is_file():
                    text += str(file) + "\n" + path.read_text(encoding="utf-8", errors="replace") + "\n\n"
        sections[name] = text
    (ROOT / "客户端" / "第三方许可.txt").write_text(shared + "\n".join(sections.values()), encoding="utf-8")
    (ROOT / "服务端" / "第三方许可.txt").write_text(shared + "\n".join(sections.values()), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging-only', action='store_true', help='仅生成发布目录，不覆盖正在使用的程序目录')
    build(staging_only=parser.parse_args().staging_only)
