"""Build the Apple Silicon server .app and drag-to-install DMG on macOS."""
import os
from pathlib import Path
import platform
import plistlib
import runpy
import shutil
import subprocess
import sys
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def run(args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def build():
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise SystemExit('此任务需要在 Apple Silicon macOS 上构建。')
    version = runpy.run_path(str(ROOT / '服务端/release_config.py'))['VERSION']
    work, dist = ROOT / '.build/macos', ROOT / 'build-output/macos'
    work.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    icon = work / 'logo.icns'
    Image.open(ROOT / '客户端/ui/assets/logo.png').save(icon, format='ICNS')
    name = '未言诗集服务端'
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
         '--name', name, '--distpath', dist, '--workpath', work / 'pyinstaller', '--specpath', work,
         '--target-arch', 'arm64', '--osx-bundle-identifier', 'com.weiyan.poetry.server', '--icon', icon,
         '--add-data', str(ROOT / '服务端/admin_ui') + ':admin_ui', '--collect-all', 'nacl',
         '--collect-all', 'webview', '--hidden-import', 'simple_websocket', ROOT / '服务端/server.py'])
    app = dist / (name + '.app')
    info_file = app / 'Contents/Info.plist'
    info = plistlib.loads(info_file.read_bytes())
    info.update(CFBundleShortVersionString=version, CFBundleVersion=version, LSMinimumSystemVersion='12.0',
                NSHighResolutionCapable=True, NSLocalNetworkUsageDescription='向你的聊天客户端提供加密消息投递服务。',
                NSAppTransportSecurity={'NSAllowsLocalNetworking': True, 'NSAllowsArbitraryLoads': True})
    info_file.write_bytes(plistlib.dumps(info))
    # Ad-hoc signing verifies bundle integrity; it is not Developer ID notarization.
    run(['codesign', '--force', '--deep', '--sign', '-', app])
    run(['codesign', '--verify', '--deep', '--strict', app])
    image = work / 'dmg-content'
    if image.exists():
        shutil.rmtree(image)
    image.mkdir()
    run(['ditto', app, image / app.name])
    os.symlink('/Applications', image / 'Applications')
    (image / '安装说明.txt').write_text('未言诗集服务端 ' + version + '\n\n将应用拖入 Applications 后打开。\n'
        '默认监听 23074。客户端默认连接 113.219.237.106:23074，需将该地址的端口转发到本机。\n'
        '此构建使用临时签名，尚未进行 Apple Developer ID 公证。首次打开时，可在系统设置 → 隐私与安全性中确认来源后允许打开。\n'
        '账号与密文队列保存在 ~/Library/Application Support/WeiyanPoetry/server，更新应用不覆盖资料。\n', encoding='utf-8')
    shutil.copy2(ROOT / 'LICENSE', image / 'LICENSE.txt')
    output = ROOT / 'build-output' / f'weiyan-server-{version}-macos-arm64.dmg'
    output.unlink(missing_ok=True)
    run(['hdiutil', 'create', '-volname', '未言诗集服务端', '-srcfolder', image, '-format', 'UDZO', '-ov', output])
    run(['hdiutil', 'verify', output])
    print(output, flush=True)


if __name__ == '__main__':
    build()
