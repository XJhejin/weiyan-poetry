"""Open a host-pinned, one-time administrator session for forum deployment."""
from pathlib import Path
import argparse
import subprocess
import zlib


def main():
    tools = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-directory', type=Path, default=tools.parent / '.build/deploy')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    folder = args.session_directory.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    known_hosts = folder / 'server-known-hosts'
    known_hosts.write_bytes((tools / 'server-known-hosts').read_bytes())
    bootstrap = zlib.compress((tools / 'session_gateway.py').read_bytes(), 9).hex()
    script = (tools / 'session.ps1.in').read_text('utf-8')
    script = script.replace('@@BOOTSTRAP_HEX@@', bootstrap)
    script = script.replace('@@KNOWN_HOSTS@@', str(known_hosts).replace("'", "''"))
    target = folder / 'forum-session.ps1'
    target.write_text(script, 'utf-8-sig')
    print('Prepared: ' + str(target), flush=True)
    if not args.prepare_only:
        # Keep the launcher alive so desktop process cleanup cannot kill SSH.
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(target)],
                       creationflags=subprocess.CREATE_NEW_CONSOLE, check=True)


if __name__ == '__main__':
    main()
