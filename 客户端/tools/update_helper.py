import ctypes
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    try:
        from update import apply_update
        apply_update(sys.argv[1])
    except Exception as error:
        if len(sys.argv) > 1:
            (Path(sys.argv[1]).parent / "result.txt").write_text(str(error), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, "更新未完成，原程序已保留。\n" + str(error), "未言诗集 · 软件更新", 16)
