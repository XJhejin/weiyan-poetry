"""Content-blind relay protocol. Only public device keys and queued ciphertext."""
import base64
import hashlib
import json
import re
import secrets
import time
import groups

from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from nacl.exceptions import RuntimeError as SodiumRuntimeError
from nacl.bindings import crypto_scalarmult

DOMAIN = b"WeiyanPoetry/envelope/v2\0"
DEVICE_DOMAIN = b"WeiyanPoetry/device/v2\0"
MAX_PACKET = 48 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decoded(value, length=None):
    if not isinstance(value, str):
        raise ValueError("加密字段格式不正确。")
    raw = base64.b64decode(value, validate=True)
    if length is not None and len(raw) != length:
        raise ValueError("加密字段长度不正确。")
    return raw


def initialize(db, data):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),box_key TEXT NOT NULL,sign_key TEXT NOT NULL,name TEXT NOT NULL,created_at REAL NOT NULL,last_seen REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS devices_user ON devices(user_id);
    CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY AUTOINCREMENT,sender_id TEXT NOT NULL,client_id TEXT NOT NULL,conversation_id TEXT NOT NULL,digest TEXT NOT NULL,created_at REAL NOT NULL,UNIQUE(sender_id,client_id));
    CREATE TABLE IF NOT EXISTS relay_packets(id INTEGER PRIMARY KEY REFERENCES receipts(id),envelope BLOB NOT NULL);
    CREATE TABLE IF NOT EXISTS deliveries(packet_id INTEGER NOT NULL REFERENCES relay_packets(id) ON DELETE CASCADE,device_id TEXT NOT NULL REFERENCES devices(id),PRIMARY KEY(packet_id,device_id));
    """)
    db.execute("INSERT OR IGNORE INTO settings VALUES ('server_id',?)", (secrets.token_hex(16),))
    db.execute("INSERT OR IGNORE INTO settings VALUES ('registration','1')")
    # Pending ciphertext has no expiry or configured storage quota. Preserve
    # existing packets when upgrading from the earlier expiring queue schema.
    if 'expires' in {r[1] for r in db.execute('PRAGMA table_info(relay_packets)')}:
        db.execute('ALTER TABLE relay_packets DROP COLUMN expires')
    db.execute("DELETE FROM settings WHERE key IN ('retention_days','queue_limit_mb')")
    columns = {r[1] for r in db.execute("PRAGMA table_info(users)")}
    if "disabled" not in columns:
        db.execute("ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0")
    db.commit()
    from migration import archive_legacy
    archive_legacy(db, data, setting(db, "server_id"))
    legacy = int(setting(db, "legacy_sequence", "0"))
    if legacy:
        row = db.execute("SELECT seq FROM sqlite_sequence WHERE name='receipts'").fetchone()
        if not row:
            db.execute("INSERT INTO sqlite_sequence VALUES ('receipts',?)", (legacy,))
        elif row[0] < legacy:
            db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='receipts'", (legacy,))
    cleanup(db)


def setting(db, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def privacy(db):
    return {"end_to_end": True, "history": "client_only", "offline_ciphertext": True,
            "delete_after_delivery": True, "retention_days": None, "queue_limit_mb": None,
            "retention": "until_received", "metadata_visible": True}


def cleanup(db):
    db.execute("PRAGMA secure_delete=ON")
    db.execute("DELETE FROM relay_packets WHERE NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.packet_id=relay_packets.id)")
    db.execute("DELETE FROM receipts WHERE created_at<? AND id NOT IN(SELECT id FROM relay_packets)", (time.time() - 30 * 86400,))


def directory(db, cid):
    room = cid.startswith("room:")
    sql = "SELECT d.id device_id,d.user_id,d.box_key,d.sign_key,d.name,u.nickname FROM devices d JOIN users u ON u.id=d.user_id WHERE d.revoked=0 AND u.disabled=0"
    if not room:
        sql += " AND EXISTS (SELECT 1 FROM members m WHERE m.conversation_id=? AND m.user_id=d.user_id)"
    rows = db.execute(sql + " ORDER BY d.user_id,d.id", () if room else (cid,)).fetchall()
    return [dict(r) for r in rows]


def receive_directory(db, cid, uid, device_id, error):
    if not db.execute('SELECT 1 FROM devices WHERE id=? AND user_id=? AND revoked=0', (device_id, uid)).fetchone():
        raise error('设备无权读取此离线队列。', 403)
    result = directory(db, cid)
    known = {d['device_id'] for d in result}
    # A queued signature remains verifiable after its sender leaves the group.
    # Scope historic public keys to packets still addressed to this exact device.
    rows = db.execute('SELECT p.envelope FROM relay_packets p JOIN deliveries r ON r.packet_id=p.id WHERE r.device_id=?', (device_id,))
    for row in rows:
        packet = json.loads(row['envelope'])
        if packet['conversation_id'] != cid or packet['device_id'] in known:
            continue
        device = db.execute('SELECT d.id device_id,d.user_id,d.box_key,d.sign_key,d.name,u.nickname FROM devices d JOIN users u ON u.id=d.user_id WHERE d.id=? AND d.user_id=? AND d.revoked=0 AND u.disabled=0', (packet['device_id'], packet['sender_id'])).fetchone()
        if device:
            result.append({**dict(device), 'historical': True})
            known.add(device['device_id'])
    return result


def register_device(db, uid, payload, error):
    try:
        device = {k: payload[k] for k in ("device_id", "box_key", "sign_key", "name")}
        if not isinstance(device["device_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", device["device_id"]):
            raise ValueError()
        if not isinstance(device["name"], str) or len(device["name"]) > 64:
            raise ValueError()
        crypto_scalarmult(secrets.token_bytes(32), decoded(device["box_key"], 32))
        VerifyKey(decoded(device["sign_key"], 32)).verify(DEVICE_DOMAIN + canonical(device), decoded(payload["proof"], 64))
    except (KeyError, ValueError, TypeError, BadSignatureError, SodiumRuntimeError):
        raise error("设备密钥或签名无效。")
    old = db.execute("SELECT * FROM devices WHERE id=?", (device["device_id"],)).fetchone()
    if old:
        if old["user_id"] != uid or old["box_key"] != device["box_key"] or old["sign_key"] != device["sign_key"] or old["revoked"]:
            raise error("此设备密钥已撤销或不匹配，请保留原数据并联系管理员。", 409)
        db.execute("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), device["device_id"]))
    else:
        if db.execute("SELECT COUNT(*) FROM devices WHERE user_id=? AND revoked=0", (uid,)).fetchone()[0] >= 8:
            raise error("每个账号最多启用 8 台设备，请先在服务端撤销旧设备。")
        if db.execute("SELECT COUNT(*) FROM devices WHERE revoked=0").fetchone()[0] >= 512:
            raise error("当前服务端设备数已达上限。")
        db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?,?,0)", (device["device_id"], uid, device["box_key"], device["sign_key"], device["name"], time.time(), time.time()))
    return {"ok": True, "device_id": device["device_id"]}


def relay(db, uid, payload, access, error, events):
    db.execute('BEGIN IMMEDIATE')
    packet = payload.get("envelope")
    if not isinstance(packet, dict) or set(packet) != {"v", "conversation_id", "sender_id", "device_id", "client_id", "ciphertext", "keys", "signature"}:
        raise error("仅接受端到端加密消息。")
    cid = packet["conversation_id"]
    if not isinstance(cid, str) or len(cid) > 128:
        raise error("会话格式不正确。")
    access(db, cid, uid)
    if packet["v"] != 2 or packet["sender_id"] != uid or not isinstance(packet["client_id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", packet["client_id"]):
        raise error("加密消息身份不正确。")
    device = db.execute("SELECT * FROM devices WHERE id=? AND user_id=? AND revoked=0", (packet["device_id"], uid)).fetchone()
    if device is None:
        raise error("请先注册本机加密设备。", 403)
    raw = canonical(packet)
    if len(raw) > MAX_PACKET:
        raise error("加密消息过大。", 413)
    try:
        cipher = decoded(packet["ciphertext"])
        if len(cipher) < 40 or not isinstance(packet["keys"], dict) or not packet["keys"]:
            raise ValueError()
        for value in packet["keys"].values():
            decoded(value, 80)
        signed = {k: v for k, v in packet.items() if k != "signature"}
        VerifyKey(decoded(device["sign_key"], 32)).verify(DOMAIN + canonical(signed), decoded(packet["signature"], 64))
    except (ValueError, TypeError, BadSignatureError):
        raise error("加密消息签名无效。")
    digest = hashlib.sha256(raw).hexdigest()
    old = db.execute("SELECT * FROM receipts WHERE sender_id=? AND client_id=?", (uid, packet["client_id"])).fetchone()
    if old:
        if old["digest"] != digest or old["conversation_id"] != cid:
            raise error("重复消息内容不一致。", 409)
        return {"id": old["id"], "queued": True}
    targets = directory(db, cid)
    groups.check_speaking(db, cid, uid, error)
    # An old directory may omit newly added devices. Reject rather than send a
    # message that the UI incorrectly describes as available on every device.
    if set(packet["keys"]) != {d["device_id"] for d in targets}:
        raise error("会话设备列表已更新，请点击加密标识核对后重试。", 409)
    cleanup(db)
    cursor = db.execute("INSERT INTO receipts(sender_id,client_id,conversation_id,digest,created_at) VALUES (?,?,?,?,?)", (uid, packet["client_id"], cid, digest, time.time()))
    mid = cursor.lastrowid
    recipients = [d for d in targets if d["device_id"] != packet["device_id"]]
    if recipients:
        db.execute("INSERT INTO relay_packets VALUES (?,?)", (mid, raw))
        db.executemany("INSERT INTO deliveries VALUES (?,?)", [(mid, d["device_id"]) for d in recipients])
    db.commit()
    events.publish({"type": "relay", "id": mid}, {d["user_id"] for d in recipients})
    return {"id": mid, "queued": bool(recipients)}


def inbox(db, uid, device_id, error, after=0):
    if not db.execute("SELECT 1 FROM devices WHERE id=? AND user_id=? AND revoked=0", (device_id, uid)).fetchone():
        raise error("设备无权读取此离线队列。", 403)
    cleanup(db)
    rows = db.execute("SELECT p.id,p.envelope FROM relay_packets p JOIN deliveries d ON d.packet_id=p.id WHERE d.device_id=? AND p.id>? ORDER BY p.id LIMIT 12", (device_id, after)).fetchall()
    result, size = [], 0
    for row in rows:
        if size > 12 * 1024 * 1024:
            break
        result.append({"id": row["id"], "envelope": json.loads(row["envelope"])})
        size += len(row["envelope"])
    db.execute("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), device_id))
    return {"items": result}


def received(db, uid, payload, error):
    did, mid = payload.get("device_id"), payload.get("id")
    if not isinstance(did, str) or not isinstance(mid, int) or isinstance(mid, bool):
        raise error("回执格式不正确。")
    if not db.execute("SELECT 1 FROM devices WHERE id=? AND user_id=? AND revoked=0", (did, uid)).fetchone():
        raise error("设备无权确认此消息。", 403)
    db.execute("DELETE FROM deliveries WHERE device_id=? AND packet_id=?", (did, mid))
    cleanup(db)
    return {"ok": True}
