"""Move legacy plaintext history to a DPAPI encrypted client-side archive."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets


def protect(raw, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, dest = Blob(len(raw), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "Weiyan Poetry legacy import", None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise RuntimeError("旧记录加密迁移失败，已停止升级以保留原数据。")
    try:
        return ctypes.string_at(dest.pbData, dest.cbData)
    finally:
        kernel.LocalFree(dest.pbData)


def archive_legacy(db, data, server_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='messages'").fetchone():
        return
    rows = db.execute("SELECT m.*,u.nickname,u.username,u.avatar,u.color FROM messages m JOIN users u ON u.id=m.sender_id").fetchall()
    images, members, messages = {}, {}, []
    for row in db.execute("SELECT * FROM members"):
        members.setdefault(row["user_id"], []).append(row["conversation_id"])
    for row in rows:
        messages.append({"id": row["id"], "conversation_id": row["conversation_id"], "body": row["body"], "image": "/uploads/" + row["image_id"] if row["image_id"] else None,
                         "client_id": row["client_id"], "created_at": row["created_at"], "sender": {"id": row["sender_id"], "nickname": row["nickname"], "username": row["username"], "avatar": row["avatar"], "color": row["color"]}})
    uploads = db.execute("SELECT * FROM uploads").fetchall()
    for row in uploads:
        file = (data / "uploads" / row["filename"]).resolve()
        if file.is_relative_to((data / "uploads").resolve()) and file.is_file():
            images[row["id"]] = {"data": base64.b64encode(file.read_bytes()).decode(), "mime": row["mime"]}
    if messages:
        destination = Path(os.environ.get("WEIYAN_MIGRATION_DIR", str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WeiyanPoetry" / "client" / "legacy")))
        if destination.resolve().is_relative_to(data.resolve()):
            raise RuntimeError("旧记录迁移目录必须位于服务端数据目录之外。")
        destination.mkdir(parents=True, exist_ok=True)
        raw = json.dumps({"server_id": server_id, "messages": messages, "images": images, "members": members}, ensure_ascii=False).encode()
        encrypted = protect(raw)
        if protect(encrypted, True) != raw:
            raise RuntimeError("旧记录迁移校验失败，原数据库未修改。")
        archive = destination / (server_id + "-" + secrets.token_hex(8) + ".dpapi")
        with archive.open("xb") as file:
            file.write(encrypted)
            file.flush()
            os.fsync(file.fileno())
        if protect(archive.read_bytes(), True) != raw:
            raise RuntimeError("旧记录归档写入校验失败。")
    maximum = max((row["id"] for row in rows), default=0)
    db.execute("PRAGMA secure_delete=ON")
    db.execute("DROP TABLE messages")
    db.execute("DROP TABLE uploads")
    # Preserve monotonically increasing IDs for imported local histories.
    db.execute("INSERT OR REPLACE INTO settings VALUES ('legacy_sequence',?)", (str(maximum),))
    db.commit()
    for row in uploads:
        file = (data / "uploads" / row["filename"]).resolve()
        if file.is_relative_to((data / "uploads").resolve()) and file.is_file():
            file.unlink()
    folder = data / "uploads"
    if folder.exists() and not any(folder.iterdir()):
        folder.rmdir()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("VACUUM")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
