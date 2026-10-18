"""Sign the Android release locally; retain the signing identity outside Git."""
import argparse
import os
from pathlib import Path
import runpy
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '客户端'))
from secure import protect


def sign(sdk, java, source):
    version = runpy.run_path(str(ROOT / '客户端/release_config.py'))['VERSION']
    private = Path(os.environ['LOCALAPPDATA']) / 'WeiyanPoetry/maintainer'
    private.mkdir(parents=True, exist_ok=True)
    store, secret = private / 'android-release.jks', private / 'android-password.dpapi'
    if store.exists() != secret.exists():
        raise SystemExit('Android 签名资料不完整，请恢复原签名资料，不能重新生成身份。')
    env = os.environ.copy()
    password = protect(secret.read_bytes(), True).decode() if secret.exists() else secrets.token_urlsafe(40)
    env['WEIYAN_APK_SIGN_PASSWORD'] = password
    if not store.exists():
        secret.write_bytes(protect(password.encode()))
        subprocess.run([str(java / 'bin/keytool.exe'), '-genkeypair', '-keystore', str(store), '-storetype', 'JKS',
                        '-alias', 'weiyan', '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
                        '-dname', 'CN=Weiyan Poetry, O=Weiyan Poetry', '-storepass:env', 'WEIYAN_APK_SIGN_PASSWORD',
                        '-keypass:env', 'WEIYAN_APK_SIGN_PASSWORD'], check=True, env=env)
    tools = sdk / 'build-tools/35.0.0'
    work = ROOT / '.build/android-sign'
    work.mkdir(parents=True, exist_ok=True)
    aligned = work / 'aligned.apk'
    output = ROOT / 'build-output' / f'weiyan-client-{version}-android.apk'
    subprocess.run([str(tools / 'zipalign.exe'), '-P', '16', '-f', '4', str(source), str(aligned)], check=True)
    env['JAVA_HOME'] = str(java)
    subprocess.run([str(tools / 'apksigner.bat'), 'sign', '--ks', str(store), '--ks-key-alias', 'weiyan',
                    '--ks-pass', 'env:WEIYAN_APK_SIGN_PASSWORD', '--key-pass', 'env:WEIYAN_APK_SIGN_PASSWORD',
                    '--out', str(output), str(aligned)], check=True, env=env)
    subprocess.run([str(tools / 'apksigner.bat'), 'verify', '--verbose', '--print-certs', str(output)], check=True, env=env)
    subprocess.run([str(tools / 'zipalign.exe'), '-c', '-P', '16', '4', str(output)], check=True)
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--java', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT / '客户端/android/app/build/outputs/apk/release/app-release-unsigned.apk')
    args = parser.parse_args()
    sign(args.sdk, args.java, args.source)
