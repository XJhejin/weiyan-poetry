"""GitHub Releases updater. Accept only artifacts signed by the pinned release key."""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from release_config import VERSION, REPOSITORY, RELEASE_PUBLIC_KEY

MAX_DOWNLOAD = 250 * 1024 * 1024
MANIFEST_DOMAIN = b"WeiyanPoetry/release/v1\0"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("发布版本号格式无效。")
    return tuple(int(part) for part in value.split("."))


def verify_manifest(data, public_key=RELEASE_PUBLIC_KEY):
    signed = {k: v for k, v in data.items() if k != "signature"}
    try:
        VerifyKey(base64.b64decode(public_key, validate=True)).verify(MANIFEST_DOMAIN + canonical(signed), base64.b64decode(data["signature"], validate=True))
    except (KeyError, ValueError, TypeError, BadSignatureError) as error:
        raise ValueError("更新签名验证失败，已阻止安装。") from error
    if signed.get("repository") != REPOSITORY or signed.get("format") != 1:
        raise ValueError("更新包不属于当前项目。")
    version(signed.get("version"))
    return signed


def validate_archive(path, kind):
    root = "客户端" if kind == "client" else "服务端"
    entry = "未言诗集.exe" if kind == "client" else "未言诗集服务端.exe"
    seen, total = set(), 0
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            name = item.filename
            parts = PurePosixPath(name).parts
            if not parts or parts[0] != root or "\\" in name or any(p in ("..", ".") or ":" in p or p.endswith((".", " ")) for p in parts):
                raise ValueError("更新包包含不安全路径。")
            if len(parts) == 1:
                continue
            if parts[1] not in (entry, "_internal", "第三方许可.txt", "启动客户端.cmd", "启动服务端.cmd"):
                raise ValueError("更新包试图覆盖程序以外的数据。")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("更新包包含不安全链接。")
            key = name.casefold().rstrip("/")
            if key in seen:
                raise ValueError("更新包包含重复文件路径。")
            seen.add(key)
            total += item.file_size
            if total > 700 * 1024 * 1024 or len(seen) > 10000:
                raise ValueError("更新包解压大小超出限制。")
        if f"{root}/{entry}".casefold() not in seen or not any(n.startswith((root + "/_internal/").casefold()) for n in seen):
            raise ValueError("更新包缺少必要程序文件。")
    return root, entry


class Updater:
    def __init__(self, kind, profile, before_exit=None):
        self.kind, self.profile = kind, Path(profile)
        self.before_exit = before_exit
        self.release = None
        self.lock = threading.Lock()
        self.progress = {"stage": "idle", "message": "", "percent": 0}

    def info(self):
        return {"current": VERSION, "repository": REPOSITORY, "source": "GitHub Releases", "signed": True}

    def fetch(self, url, maximum=2 * 1024 * 1024):
        if not url.startswith(("https://api.github.com/", "https://github.com/")):
            raise ValueError("更新地址不是项目 GitHub 地址。")
        binary = '/releases/assets/' in url or 'api.github.com' not in url
        req = urllib.request.Request(url, headers={"User-Agent": "WeiyanPoetry/" + VERSION, "Accept": "application/octet-stream" if binary else "application/vnd.github+json", 'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                raw = response.read(maximum + 1)
                if len(raw) > maximum:
                    raise ValueError("更新信息大小异常。")
                return raw
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ValueError("项目尚未发布可用版本，请稍后再检查。") from error
            if error.code in (403, 429):
                raise ValueError("GitHub 访问频率受限，请稍后重试。") from error
            raise ValueError("GitHub 暂时无法提供更新。") from error
        except (OSError, urllib.error.URLError) as error:
            raise ValueError("暂时无法连接 GitHub，请检查网络后重试。") from error

    def check(self):
        if REPOSITORY.startswith("PENDING/"):
            return {**self.info(), "available": False, "latest": None, "message": "项目正在准备首次 GitHub 发布。"}
        release = json.loads(self.fetch("https://api.github.com/repos/" + REPOSITORY + "/releases/latest"))
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("当前发布不是正式版本。")
        assets = {asset["name"]: asset for asset in release.get("assets", [])}
        manifest_asset = assets.get("weiyan-update.json")
        if not manifest_asset:
            raise ValueError("该版本缺少签名更新清单，暂时无法安装。")
        manifest = json.loads(self.fetch(manifest_asset["url"], 128 * 1024))
        signed = verify_manifest(manifest)
        if release.get("tag_name") != "v" + signed["version"]:
            raise ValueError("版本标签与签名清单不一致。")
        artifact = signed["assets"].get(self.kind)
        if not isinstance(artifact, dict) or not re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", "")) or not isinstance(artifact.get("bytes"), int) or not 0 < artifact["bytes"] <= MAX_DOWNLOAD:
            raise ValueError("更新文件校验信息无效。")
        asset = assets.get(artifact["name"])
        if not asset or asset.get("size") != artifact["bytes"]:
            raise ValueError("更新文件大小与签名清单不一致。")
        available = version(signed["version"]) > version(VERSION)
        self.release = {"manifest": manifest, "artifact": artifact, "asset": asset, "version": signed["version"]}
        return {**self.info(), "available": available, "latest": signed["version"], "notes": release.get("body", "")[:16000],
                "message": "发现新版本，可下载并安装。" if available else "当前已是最新正式版本。"}

    def install(self):
        if not getattr(sys, "frozen", False):
            raise ValueError("源码预览支持检查更新；请在打包后的桌面软件中安装更新。")
        with self.lock:
            if self.progress["stage"] in ("downloading", "verifying", "installing"):
                return self.progress
            info = self.check()
            if not info["available"]:
                raise ValueError(info["message"])
            self.progress = {"stage": "downloading", "percent": 0, "message": "正在下载签名更新包…"}
            threading.Thread(target=self._download, daemon=True).start()
            return {"started": True}

    def _download(self):
        folder = self.profile / "updates" / (self.kind + "-" + secrets.token_hex(8))
        try:
            folder.mkdir(parents=True)
            artifact = self.release["artifact"]
            url = self.release["asset"]["url"]
            if not url.startswith("https://api.github.com/repos/" + REPOSITORY + "/releases/assets/"):
                raise ValueError("更新文件来源与项目不一致。")
            req = urllib.request.Request(url, headers={"User-Agent": "WeiyanPoetry/" + VERSION, 'Accept': 'application/octet-stream', 'X-GitHub-Api-Version': '2022-11-28'})
            archive, digest, size = folder / "package.zip", hashlib.sha256(), 0
            with urllib.request.urlopen(req, timeout=30) as response, archive.open("xb") as file:
                while True:
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > artifact["bytes"]:
                        raise ValueError("下载文件大小异常，已停止更新。")
                    file.write(chunk)
                    digest.update(chunk)
                    self.progress = {"stage": "downloading", "percent": int(size * 100 / artifact["bytes"]), "message": f"正在下载 {size // 1024} / {artifact['bytes'] // 1024} KB"}
            self.progress = {"stage": "verifying", "percent": 100, "message": "正在校验更新包…"}
            if size != artifact["bytes"] or digest.hexdigest() != artifact["sha256"]:
                raise ValueError("更新包完整性校验失败，已阻止安装。")
            validate_archive(archive, self.kind)
            helper = Path(__file__).resolve().parent / "update_helper.exe"
            if not helper.is_file():
                raise ValueError("缺少更新辅助程序，请重新下载完整软件。")
            shutil.copy2(helper, folder / "install.exe")
            target = Path(sys.executable).resolve().parent
            job = {"kind": self.kind, "manifest": self.release["manifest"], "target": str(target), "parent_pid": os.getpid(), "archive": str(archive)}
            (folder / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            subprocess.Popen([str(folder / "install.exe"), str(folder / "job.json")], creationflags=subprocess.CREATE_NO_WINDOW, cwd=str(folder))
            self.progress = {"stage": "installing", "percent": 100, "message": "验证通过，正在重启安装…"}
            time.sleep(1.5)
            if self.before_exit:
                self.before_exit()
            os._exit(0)
        except Exception as error:
            self.progress = {"stage": "error", "percent": 0, "message": str(error) or "更新未完成，原程序可继续使用。"}


def apply_update(job_path):
    """Runs in a copied standalone helper after the old app has exited."""
    folder = Path(job_path).resolve().parent
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    target = Path(job["target"]).resolve()
    archive = Path(job["archive"]).resolve()
    if archive.parent != folder or target == target.parent or not target.is_dir() or target.is_symlink():
        raise ValueError("安装目录无效。")
    signed = verify_manifest(job["manifest"])
    artifact = signed["assets"][job["kind"]]
    if archive.stat().st_size != artifact["bytes"] or hashlib.sha256(archive.read_bytes()).hexdigest() != artifact["sha256"]:
        raise ValueError("安装前复核失败。")
    root, entry = validate_archive(archive, job["kind"])
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, int(job["parent_pid"]))
    if handle:
        try:
            if kernel.WaitForSingleObject(handle, 90000) != 0:
                raise ValueError("旧程序尚未退出，请关闭窗口后重试更新。")
        finally:
            kernel.CloseHandle(handle)
    unpacked, backup = folder / "unpacked", folder / "backup"
    unpacked.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        package.extractall(unpacked)
    source = unpacked / root
    backup.mkdir(exist_ok=True)
    changed = []
    try:
        for name in (entry, "_internal", "第三方许可.txt", "启动客户端.cmd", "启动服务端.cmd"):
            fresh, old = source / name, target / name
            if not fresh.exists():
                continue
            if old.is_symlink() or (old.exists() and old.resolve().parent != target):
                raise ValueError("目标程序路径无效。")
            if old.exists():
                old.rename(backup / name)
            changed.append(name)
            shutil.move(str(fresh), str(old))
        subprocess.Popen([str(target / entry)], cwd=str(target))
        (folder / "result.txt").write_text("更新成功 " + signed["version"], encoding="utf-8")
    except Exception:
        for name in reversed(changed):
            installed = target / name
            if installed.is_dir():
                shutil.rmtree(installed)
            elif installed.exists():
                installed.unlink()
            if (backup / name).exists():
                shutil.move(str(backup / name), str(installed))
        subprocess.Popen([str(target / entry)], cwd=str(target))
        raise
