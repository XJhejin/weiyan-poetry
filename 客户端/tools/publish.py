"""Publish signed Windows, Android and Apple Silicon releases; keys stay local."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '客户端'))
from secure import protect
from update import MANIFEST_DOMAIN, canonical, validate_archive, verify_manifest
from nacl.signing import SigningKey


def executable(name, fallback):
    found = shutil.which(name)
    if found:
        return found
    for path in fallback:
        if path.is_file():
            return str(path)
    raise SystemExit(f'找不到 {name}，请先安装并加入 PATH。')


def run(args, capture=False):
    result = subprocess.run(args, cwd=ROOT, check=True, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ''


def prepare():
    configs = [runpy.run_path(str(ROOT / folder / 'release_config.py')) for folder in ('客户端', '服务端')]
    values = [{k: c[k] for k in ('VERSION', 'REPOSITORY', 'RELEASE_PUBLIC_KEY')} for c in configs]
    if values[0] != values[1] or values[0]['REPOSITORY'].startswith('PENDING/'):
        raise SystemExit('请先统一客户端和服务端的版本、仓库与发布公钥。')
    if (ROOT / '客户端/update.py').read_bytes() != (ROOT / '服务端/update.py').read_bytes():
        raise SystemExit('两份更新程序必须一致。')
    config = values[0]
    key_file = Path(os.environ['LOCALAPPDATA']) / 'WeiyanPoetry/maintainer/release-signing.dpapi'
    key = SigningKey(protect(key_file.read_bytes(), decrypt=True))
    if base64.b64encode(bytes(key.verify_key)).decode() != config['RELEASE_PUBLIC_KEY']:
        raise SystemExit('本机发布私钥与软件内置公钥不匹配。')
    folder = ROOT / '.releases' / config['VERSION']
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {'format': 1, 'repository': config['REPOSITORY'], 'version': config['VERSION'], 'assets': {}}
    files = []
    for kind, label, build_name, entry, command in (
        ('client', '客户端', '未言诗集', '未言诗集.exe', '启动客户端.cmd'),
        ('server', '服务端', '未言诗集服务端', '未言诗集服务端.exe', '启动服务端.cmd'),
    ):
        source = ROOT / 'build-output' / build_name
        if not (source / entry).is_file() or not (source / '_internal/update_helper.exe').is_file():
            raise SystemExit('缺少打包程序，请先运行 build.py 并完成集成测试。')
        name = f'weiyan-{kind}-{config["VERSION"]}-windows-x64.zip'
        archive = folder / name
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
            for item in [source / entry] + sorted((source / '_internal').rglob('*')):
                if item.is_symlink():
                    raise SystemExit('构建目录包含符号链接，已停止发布。')
                if item.is_file():
                    package.write(item, label + '/' + item.relative_to(source).as_posix())
            for extra in (command, '第三方许可.txt'):
                package.write(ROOT / label / extra, label + '/' + extra)
        validate_archive(archive, kind)
        with archive.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        manifest['assets'][kind] = {'name': name, 'bytes': archive.stat().st_size, 'sha256': digest}
        files.append(archive)
        print(f'{label}: {archive.name} ({archive.stat().st_size / 1048576:.1f} MB)', flush=True)
    for kind, name in (
        ('client_windows_msi', f'weiyan-client-{config["VERSION"]}-windows-x64.msi'),
        ('client_android', f'weiyan-client-{config["VERSION"]}-android.apk'),
        ('server_macos_arm64', f'weiyan-server-{config["VERSION"]}-macos-arm64.dmg'),
    ):
        source = ROOT / 'build-output' / name
        if not source.is_file():
            raise SystemExit('缺少已验证的平台安装包：' + name)
        target = folder / name
        shutil.copy2(source, target)
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        manifest['assets'][kind] = {'name': name, 'bytes': target.stat().st_size, 'sha256': digest}
        files.append(target)
        print(f'{name} ({target.stat().st_size / 1048576:.1f} MB)', flush=True)
    manifest['signature'] = base64.b64encode(key.sign(MANIFEST_DOMAIN + canonical(manifest)).signature).decode()
    verify_manifest(manifest)
    manifest_file = folder / 'weiyan-update.json'
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return config, files + [manifest_file]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true', help='只生成本机签名更新包，不上传')
    args = parser.parse_args()
    config, files = prepare()
    if args.prepare_only:
        print('签名校验通过。尚未上传 GitHub。')
        return
    git = executable('git', [Path(os.environ['LOCALAPPDATA']) / 'Programs/WorkBuddy/resources/vendor/PortableGit/cmd/git.exe'])
    gh = executable('gh', [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'GitHub CLI/gh.exe'])
    if run([git, 'status', '--porcelain'], True):
        raise SystemExit('请先提交并推送所有源码修改，再发布与该提交对应的软件。')
    commit = run([git, 'rev-parse', 'HEAD'], True)
    branch = run([git, 'branch', '--show-current'], True)
    remote = run([git, 'ls-remote', 'origin', 'refs/heads/' + branch], True).split()
    if not remote or remote[0] != commit:
        raise SystemExit('当前提交尚未同步到 origin；请先推送项目分支。')
    notes_file = files[0].parent / 'release-notes.md'
    notes_file.write_text(f'''未言诗集 {config['VERSION']}

安装包：
- Windows 客户端：`weiyan-client-{config['VERSION']}-windows-x64.msi`。安装后自动创建桌面与开始菜单快捷方式。
- 安卓客户端：`weiyan-client-{config['VERSION']}-android.apk`。Android 7.0+，ARM64 或 x86_64；由维护者固定证书签名。
- Apple Silicon Mac 服务端：`weiyan-server-{config['VERSION']}-macos-arm64.dmg`。打开后拖入 Applications。
- Windows 客户端与服务端便携 ZIP 继续提供；完整解压并保留 `_internal`，无需安装 Python。

功能：
- 个人与群头像图片选择、拖动裁剪及缩放；服务端同步头像，管理页可查看并重置成员头像。
- 单一公共房间「-扉页-」，好友申请与同意，@用户名/昵称搜索，可直接与非好友私聊。
- 群聊邀请、群主与管理员、踢出、群名称/头像/公告、禁言、群昵称、群主转让、退群与解散。
- 文字、图片、文件、视频端到端加密；支持视频预览、播放和拖入附件。图片最多 5 MB，文件/视频每项最多 20 MB。
- Windows 关闭窗口后留在托盘；有未读时整枚图标闪烁，悬停显示最新消息预览；系统通知使用通用提示。可从客户端设置前往 Windows 任务栏设置固定图标。安卓使用可停止的前台后台接收服务，通知不显示聊天正文。
- 本机加密记录、草稿、收藏与待发内容；服务端未送达密文永久保留，无软件总容量配额，确认接收后删除。
- 两端提供 GitHub 更新页；校验 Ed25519 清单签名与 SHA-256。安卓还校验应用签名证书。

默认服务端端口 23074；发布客户端默认连接 `http://113.219.237.106:23074`。此前保存的自定义地址保留。
需要在该地址实际部署服务端并开放/转发端口；软件发布不会自动部署远程服务器。公网登录凭据应通过 HTTPS 保护。

Mac 构建仅作临时签名，未取得 Apple Developer ID 公证，首次打开需在 macOS 系统设置确认来源。
安卓省电策略、强行停止或系统清理可能中断后台接收；重新打开后补收。未提供厂商云推送。
加密协议尚未独立安全审计，无双棘轮前向保密；首次通信请核对设备指纹。新设备不能恢复旧设备历史。
更新保留用户资料；Mac 服务端资料位于 `~/Library/Application Support/WeiyanPoetry/server`。

发布源码提交：{commit}
''', encoding='utf-8')
    run([gh, 'release', 'create', 'v' + config['VERSION'], '--repo', config['REPOSITORY'], '--target', commit,
         '--title', '未言诗集 ' + config['VERSION'], '--notes-file', str(notes_file), '--latest',
         *[str(file) for file in files]])


if __name__ == '__main__':
    main()
