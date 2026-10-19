"""Publish signed Windows, Android and Linux releases; keys stay local."""
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
    notes = (ROOT / 'RELEASE_NOTES.md').read_text('utf-8')
    notes_file.write_text(notes + '\n发布源码提交：' + commit + '\n', encoding='utf-8')
    run([gh, 'release', 'create', 'v' + config['VERSION'], '--repo', config['REPOSITORY'], '--target', commit,
         '--title', '未言诗集 ' + config['VERSION'], '--notes-file', str(notes_file), '--latest',
         *[str(file) for file in files]])


if __name__ == '__main__':
    main()
