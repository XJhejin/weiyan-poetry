import ctypes
import json
from pathlib import Path
import sys
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        report = Path(sys.argv[2])
        try:
            import _cffi_backend
            from update import VERSION
            from nacl.signing import VerifyKey
            # RFC 8032 test vector 1 exercises the actual bundled sodium/CFFI runtime.
            VerifyKey(bytes.fromhex('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')).verify(
                b'', bytes.fromhex('e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f'
                                   'b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'))
            result = {'status': 'ok', 'protocol': 1, 'version': VERSION,
                      'frozen': bool(getattr(sys, 'frozen', False)), 'signature_runtime': True}
        except Exception as error:
            result = {'status': 'error', 'message': str(error)}
        report.write_text(json.dumps(result), encoding='utf-8')
        return 0 if result['status'] == 'ok' else 1
    try:
        from update import apply_update
        apply_update(sys.argv[1])
        return 0
    except Exception as error:
        if len(sys.argv) > 1:
            (Path(sys.argv[1]).parent / "result.txt").write_text(str(error), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, "更新未完成，原程序已保留。\n" + str(error), "未言诗集 · 软件更新", 16)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
