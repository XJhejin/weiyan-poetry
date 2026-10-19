"""Build a per-user MSI with stable components, shortcuts, upgrade and rollback."""
from pathlib import Path
import argparse
import runpy
import subprocess
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WIX = 'http://wixtoolset.org/schemas/v4/wxs'
UI = 'http://wixtoolset.org/schemas/v4/wxs/ui'
ET.register_namespace('', WIX)
ET.register_namespace('ui', UI)


def add(parent, name, **attrs):
    return ET.SubElement(parent, '{' + WIX + '}' + name, {k: str(v) for k, v in attrs.items()})


def build(wix, kind='client'):
    version = runpy.run_path(str(ROOT / '客户端/release_config.py'))['VERSION']
    control = kind == 'control'
    name = 'HushLeaf 服务器控制端' if control else 'HushLeaf'
    build_name = '未言诗集服务器控制端' if control else '未言诗集'
    source_folder = '服务器控制端' if control else '客户端'
    executable_name = build_name + '.exe'
    folder = ROOT / 'build-output' / build_name
    if not (folder / executable_name).is_file():
        raise SystemExit('请先构建 ' + name + '。')
    work = ROOT / '.build' / ('control-msi' if control else 'msi')
    work.mkdir(parents=True, exist_ok=True)
    marker = work / '.install-msi'
    marker.write_text('WeiyanPoetry per-user Windows Installer\n', encoding='utf-8')
    xml = ET.Element('{' + WIX + '}Wix')
    package = add(xml, 'Package', Name=name, Manufacturer='HushLeaf', Version=version,
                  UpgradeCode='0808c609-659c-41a5-901b-f45107b59335' if control else '48b7cc57-63f1-48ac-9d06-04869d9666d7', Scope='perUser', Language='2052', Codepage='936')
    add(package, 'MajorUpgrade', DowngradeErrorMessage='已安装更新版本的未言诗集。', Schedule='afterInstallInitialize')
    add(package, 'MediaTemplate', EmbedCab='yes', CompressionLevel='high')
    local = add(package, 'StandardDirectory', Id='LocalAppDataFolder')
    programs = add(local, 'Directory', Id='UserPrograms', Name='Programs')
    target = add(programs, 'Directory', Id='INSTALLFOLDER', Name='WeiyanPoetryControl' if control else 'WeiyanPoetry')
    dirs = {'.': target}
    feature = add(package, 'Feature', Id='MainFeature', Title='未言诗集', Level='1')
    paths = [(p, p.relative_to(folder).as_posix()) for p in sorted(folder.rglob('*')) if p.is_file()]
    paths += [(ROOT / source_folder / '第三方许可.txt', '第三方许可.txt'), (marker, '.install-msi')]
    for path, relative in paths:
        parts = Path(relative).parts
        parent = target
        current = Path()
        for part in parts[:-1]:
            current /= part
            key = current.as_posix()
            if key not in dirs:
                dirs[key] = add(parent, 'Directory', Id='D' + uuid.uuid5(uuid.NAMESPACE_URL, key).hex, Name=part)
            parent = dirs[key]
        ident = uuid.uuid5(uuid.NAMESPACE_URL, 'weiyan/' + kind + '/' + relative).hex
        component = add(parent, 'Component', Id='C' + ident, Guid=str(uuid.uuid5(uuid.NAMESPACE_URL, ('weiyan/control/msi/component/' if control else 'weiyan/msi/component/') + relative)))
        file = add(component, 'File', Id='ClientExe' if relative == executable_name else 'F' + ident,
                   Source=str(path), Name=parts[-1])
        add(component, 'RegistryValue', Root='HKCU', Key='Software\\' + ('WeiyanPoetryControl' if control else 'WeiyanPoetry') + '\\InstalledFiles', Name=ident, Type='integer', Value='1', KeyPath='yes')
        if relative == executable_name:
            add(file, 'Shortcut', Id='StartShortcut', Directory='ProgramMenuFolder', Name=name, WorkingDirectory='INSTALLFOLDER', Advertise='no', Icon='AppIcon')
            add(file, 'Shortcut', Id='DesktopShortcut', Directory='DesktopFolder', Name=name, WorkingDirectory='INSTALLFOLDER', Advertise='no', Icon='AppIcon')
        add(feature, 'ComponentRef', Id='C' + ident)
    add(package, 'StandardDirectory', Id='ProgramMenuFolder')
    add(package, 'StandardDirectory', Id='DesktopFolder')
    add(package, 'Icon', Id='AppIcon', SourceFile=str(ROOT / '客户端/ui/assets/logo.ico'))
    add(package, 'Property', Id='ARPPRODUCTICON', Value='AppIcon')
    add(package, 'Property', Id='ARPURLINFOABOUT', Value='https://www.hushleaf.top/')
    ET.SubElement(package, '{' + UI + '}WixUI', {'Id': 'WixUI_Minimal'})
    rtf = work / 'license.rtf'
    license_text = (ROOT / 'LICENSE').read_text('utf-8').replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}').replace('\n', '\\par\n')
    license_text = ''.join(c if ord(c) < 128 else '\\u' + str(ord(c) if ord(c) < 32768 else ord(c) - 65536) + '?' for c in license_text)
    rtf.write_text('{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Segoe UI;}}\\f0\\fs18 ' + license_text + '}', encoding='ascii')
    add(package, 'WixVariable', Id='WixUILicenseRtf', Value=str(rtf))
    source = work / 'client.wxs'
    ET.ElementTree(xml).write(source, encoding='utf-8', xml_declaration=True)
    output = ROOT / 'build-output' / f'weiyan-{kind}-{version}-windows-x64.msi'
    subprocess.run([wix, 'build', str(source), '-arch', 'x64', '-ext', 'WixToolset.UI.wixext', '-culture', 'zh-CN', '-o', str(output)], check=True, cwd=ROOT)
    print(output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wix', default='wix')
    parser.add_argument('--kind', choices=['client', 'control'], default='client')
    args = parser.parse_args()
    build(args.wix, args.kind)
