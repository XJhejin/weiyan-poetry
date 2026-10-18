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
    configs = [runpy.run_path(str(ROOT / folder / 'release_config.py')) for folder in ('客户端', '服务端', '服务器控制端')]
    values = [{k: c[k] for k in ('VERSION', 'REPOSITORY', 'RELEASE_PUBLIC_KEY')} for c in configs]
    if any(value != values[0] for value in values[1:]) or values[0]['REPOSITORY'].startswith('PENDING/'):
        raise SystemExit('请先统一客户端和服务端的版本、仓库与发布公钥。')
    if (ROOT / '客户端/update.py').read_bytes() != (ROOT / '服务端/update.py').read_bytes():
        raise SystemExit('两份更新程序必须一致。')
    if os.name == 'nt':
        env = os.environ.copy()
        env['WEIYAN_TEST_BINARIES'] = '1'
        subprocess.run([sys.executable, str(ROOT / '客户端/tests/test_update_runtime.py')],
                       cwd=ROOT, env=env, check=True)
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
        ('control_windows_msi', f'weiyan-control-{config["VERSION"]}-windows-x64.msi'),
        ('server_linux_source', f'weiyan-server-{config["VERSION"]}-linux-source.tar.gz'),
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
- Windows 服务器控制端：`weiyan-control-{config['VERSION']}-windows-x64.msi`。独立安装，通过受限 SSH 授权管理远程服务器；安装包不包含管理私钥。
- 安卓客户端：`weiyan-client-{config['VERSION']}-android.apk`。Android 7.0+，ARM64 或 x86_64；由维护者固定证书签名。
- Apple Silicon Mac 服务端：`weiyan-server-{config['VERSION']}-macos-arm64.dmg`。打开后拖入 Applications。
- Windows 客户端与服务端便携 ZIP 继续提供；完整解压并保留 `_internal`，无需安装 Python。

功能：
- 新增两分钟内消息撤回，支持文字、图片、文件与视频，离线消息同步撤回。
- 同账号多设备在双方核对指纹后加密同步每个会话最近 50 条记录及附件，自己发送的新消息同步到其他已登记设备。
- Android 新增前台横幅与后台系统消息弹窗，点击可进入会话；不展示聊天正文并遵循免打扰设置。
- 2.1.3 将 Windows 和安卓客户端默认连接更新为 156.225.28.175:23074；升级时迁移旧官方默认地址，保留其他自定义地址。
- 提供 Linux 无界面运行依赖、独立数据目录、systemd 开机自启模板和不含用户资料的源码部署包。
- 新增独立服务器控制端：查看远程运行状态、内存与磁盘、账号与设备、密文队列及运行日志；支持服务重启和 GitHub 签名更新，启动失败恢复原程序及更新前数据库。
- 2.1.2 增加服务端自定义 IPv4/IPv6 监听、配置保存和准确的客户端连接地址；修改前先停止服务，启动成功后自动保存。
- 修复 Mac 服务端 GitHub 更新检查：随包提供 CA 证书，保留 HTTPS 验证；API 不可用时尝试 GitHub Releases 签名清单。旧版无法检查更新时请手动下载本页 DMG 覆盖应用一次。
- Android 后台接收开关与后台设置入口；允许接收时重启/覆盖升级恢复服务，明确停止后不自行恢复。适配状态栏及刘海/挖孔安全区域，顶部增加 8 dp，禁止双指缩放。
- 公共房间与群聊资料中新增消息免打扰开关：本机加密保存，仍接收消息并保留会话未读标记，停止该会话的通知和托盘提醒。
- 2.1.1 修复 Windows 旧版更新助手缺少 `_cffi_backend` 的启动错误；更新前检查独立助手及验签依赖，自检失败保留当前程序运行。
- 若旧版自动更新已报上述错误，请先下载本页 MSI，退出客户端后安装一次；Windows 原账号下的本机资料与连接设置保留。
- 个人与群头像图片选择、拖动裁剪及缩放；服务端同步头像，管理页可查看并重置成员头像。
- 单一公共房间「-扉页-」，好友申请与同意，@用户名/昵称搜索，可直接与非好友私聊。
- 群聊邀请、群主与管理员、踢出、群名称/头像/公告、禁言、群昵称、群主转让、退群与解散。
- 文字、图片、文件、视频端到端加密；支持视频预览、播放和拖入附件。图片最多 5 MB，文件/视频每项最多 20 MB。
- Windows 关闭窗口后留在托盘；有未读时整枚图标闪烁，悬停显示最新消息预览；系统通知使用通用提示。可从客户端设置前往 Windows 任务栏设置固定图标。安卓使用可停止的前台后台接收服务，通知不显示聊天正文。
- 本机加密记录、草稿、收藏与待发内容；服务端未送达密文永久保留，无软件总容量配额，确认接收后删除。
- 两端提供 GitHub 更新页；校验 Ed25519 清单签名与 SHA-256。安卓还校验应用签名证书。

默认服务端端口 23074；发布客户端默认连接 `https://156.225.28.175:23074`。此前保存的自定义地址保留。
该地址已部署 CentOS 无界面服务端，使用受信任的 HTTPS 证书和自动续期；服务端及代理已配置开机自启。账号与待投递密文保存在独立数据目录；管理接口不公开。

Mac 构建仅作临时签名，未取得 Apple Developer ID 公证，首次打开需在 macOS 系统设置确认来源。
安卓省电策略、强行停止或系统清理可能中断后台接收；重新打开后补收。未提供厂商云推送。
加密协议尚未独立安全审计，无双棘轮前向保密；首次通信请核对设备指纹。新设备需与保存旧记录的设备双方核对指纹并确认信任，再由旧设备上线加密同步。
更新保留用户资料；Mac 服务端资料位于 `~/Library/Application Support/WeiyanPoetry/server`。

发布源码提交：{commit}
''', encoding='utf-8')
    run([gh, 'release', 'create', 'v' + config['VERSION'], '--repo', config['REPOSITORY'], '--target', commit,
         '--title', '未言诗集 ' + config['VERSION'], '--notes-file', str(notes_file), '--latest',
         *[str(file) for file in files]])


if __name__ == '__main__':
    main()
