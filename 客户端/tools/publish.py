"""Package and sign independent Windows releases; keep the key off GitHub."""
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
    notes_file.write_text(f'''未言诗集 {config['VERSION']} · Windows 10/11 x64

分别下载客户端和服务端 ZIP，完整解压后双击其中的 exe；不需要安装 Python。

- 浅粉色聊天界面、简洁无框书页花瓣 Logo，称呼使用“掇花者”。
- 独立服务端图形管理：运行状态、账号与设备、密文存储规则。
- 文字与图片端到端加密；本机记录、草稿、收藏和待发内容加密保存。
- 未送达密文永久保留、无软件队列容量上限，接收确认后删除。
- 两端的软件更新页面连接本仓库，验证 Ed25519 发布签名和 SHA-256 后安装。

请选择 `weiyan-client-{config['VERSION']}-windows-x64.zip`（客户端）或
`weiyan-server-{config['VERSION']}-windows-x64.zip`（管理服务端）。各自的 `_internal` 文件夹必须保留。

首次使用先启动服务端，再启动客户端。同机默认地址 http://127.0.0.1:8765。
局域网使用请在服务端复制连接地址。公网部署需另配 HTTPS。

加密协议尚未经过独立安全审计，无双棘轮前向保密。首次通信请核对设备指纹。
旧记录留在原电脑；更新会保留客户端资料和服务端 data。

已完成源代码、隐私与更新边界及打包程序集成测试。
发布源码提交：{commit}
''', encoding='utf-8')
    run([gh, 'release', 'create', 'v' + config['VERSION'], '--repo', config['REPOSITORY'], '--target', commit,
         '--title', '未言诗集 ' + config['VERSION'], '--notes-file', str(notes_file), '--latest',
         *[str(file) for file in files]])


if __name__ == '__main__':
    main()
