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
    # Original open-book / paired-leaf silhouette, shared by SVG and Android.
    scale = 8
    image = Image.new("RGBA", (96 * scale, 96 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    def cubic(start, one, two, end):
        result = []
        for i in range(81):
            t = i / 80
            result.append(tuple(round(((1-t)**3*start[k] + 3*(1-t)**2*t*one[k] + 3*(1-t)*t*t*two[k] + t**3*end[k])*scale) for k in (0,1)))
        return result
    for start, one, two, end, other_one, other_two, color in (
        ((45,79),(23,73),(12,53),(18,20),(38,24),(50,42),'#C57C9D'),
        ((51,79),(73,73),(84,53),(78,20),(58,24),(46,42),'#A66486'),
    ):
        draw.polygon(cubic(start,one,two,end)+cubic(end,other_one,other_two,start),fill=color)
    for points in (((29,38),(34,48),(39,60),(42,72)),((67,38),(62,48),(57,60),(54,72))):
        draw.line(cubic(*points),fill='#FFF7FC',width=round(2.7*scale),joint='curve')
        for x,y in (points[0],points[-1]):
            r=1.35*scale;draw.ellipse((int(x*scale-r),int(y*scale-r),int(x*scale+r),int(y*scale+r)),fill='#FFF7FC')
    image = image.resize((256,256), Image.Resampling.LANCZOS)
    image.save(ASSETS / 'logo.png')
    for folder in (ASSETS, ROOT/'服务端/admin_ui', ROOT/'服务器控制端/ui'):
        image.save(folder/'logo.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])


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
