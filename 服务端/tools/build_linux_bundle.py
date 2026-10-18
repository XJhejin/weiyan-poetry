"""Build an allowlisted headless source bundle without local user data."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
import tarfile


SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from release_config import VERSION


FILES = (
    "server.py", "relay_core.py", "groups.py", "friends.py", "avatars.py",
    "listening.py", "migration.py", "release_config.py", "requirements-headless.txt", "admin.py", "update.py",
    "admin_ui/index.html", "admin_ui/admin.js", "admin_ui/admin.css", "admin_ui/motion.js", "admin_ui/logo.svg", "admin_ui/logo.ico",
    "deploy/linux/weiyan-server.service", "deploy/linux/server.env.example",
    "deploy/linux/部署说明.md", "deploy/linux/Caddyfile.example",
    "deploy/linux/weiyan-proxy.service",
    "deploy/linux/control_gateway.py",
)


def build(output: Path) -> Path:
    # Validate the complete allowlist before writing any archive.
    for name in FILES:
        source = SERVER / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Missing or symlinked deployment input: {name}")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"weiyan-server-{VERSION}-linux-source.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
        for name in FILES:
            source = SERVER / name
            info = bundle.gettarinfo(str(source), arcname=f"weiyan-server-{VERSION}/{name}")
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = 0o644
            with source.open("rb") as stream:
                bundle.addfile(info, stream)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="ascii"
    )
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SERVER.parent / "build-output" / "linux")
    args = parser.parse_args()
    archive = build(args.output.resolve())
    print(archive)
    print(archive.with_name(archive.name + ".sha256"))


if __name__ == "__main__":
    main()
