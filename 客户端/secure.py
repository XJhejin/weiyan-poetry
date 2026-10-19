"""Client-only key custody, authenticated encryption, and encrypted local history.

The relay never imports this module. Network packets use libsodium SecretBox,
sealed boxes for recipient key wrapping, and Ed25519 signatures. All private
material and history stay in a Windows-DPAPI-protected local vault.
"""
from __future__ import annotations

import base64
import ctypes
import copy
from ctypes import wintypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import quote, urlsplit

from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.secret import SecretBox
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import CryptoError, BadSignatureError
from large_files import MAX_SIZE, CHUNK_SIZE, OVERHEAD, EncryptedFiles, FileTransfers, checked_descriptor

DOMAIN = b"WeiyanPoetry/envelope/v2\0"
DEVICE_DOMAIN = b"WeiyanPoetry/device/v2\0"
MAX_FILE = MAX_SIZE


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def b64(raw):
    return base64.b64encode(raw).decode("ascii")


def unb64(value):
    return base64.b64decode(value, validate=True)


def validate_image(image):
    if not isinstance(image, dict) or not isinstance(image.get('data'), str):
        raise SecurityError('图片格式不正确。')
    raw = unb64(image['data'])
    if len(raw) > 5 * 1024 * 1024:
        raise SecurityError('较大图片请使用分片上传，最大支持 10 GB。')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif raw.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif raw.startswith((b'GIF87a', b'GIF89a')):
        mime = 'image/gif'
    elif len(raw) > 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        mime = 'image/webp'
    else:
        raise SecurityError('收到不支持的图片格式。')
    return {'data': b64(raw), 'mime': mime}


def validate_file(file):
    if not isinstance(file, dict) or not isinstance(file.get('data'), str):
        raise SecurityError('文件格式不正确。')
    raw = unb64(file['data'])
    name = file.get('name')
    if len(raw) > 20 * 1024 * 1024 or not isinstance(name, str) or not 1 <= len(name) <= 180 or any(ord(c) < 32 or c in '/\\' for c in name):
        raise SecurityError('文件名无效，或需要使用分片上传（最大 10 GB）。')
    kind, mime = 'file', 'application/octet-stream'
    if file.get('kind') == 'video':
        if len(raw) >= 12 and raw[4:8] == b'ftyp':
            kind, mime = 'video', 'video/mp4'
        elif raw.startswith(b'\x1aE\xdf\xa3'):
            kind, mime = 'video', 'video/webm'
        else:
            raise SecurityError('视频预览支持 MP4 和 WebM 格式，请将其他格式作为普通文件发送。')
    return {'data': b64(raw), 'name': name, 'size': len(raw), 'kind': kind, 'mime': mime}


class SecurityError(ValueError):
    pass


class RemoteError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def protect(raw, decrypt=False):
    """Use the platform account key; never fall back to an unprotected key."""
    if os.environ.get("WEIYAN_PLATFORM") == "android":
        from java import jclass
        return bytes(jclass("com.weiyan.poetry.KeyVault").protect(raw, decrypt))
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source = Blob(len(raw), buffer)
    dest = Blob()
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
        ok = fn(ctypes.byref(source), "Weiyan Poetry local vault", None, None, None, 1, ctypes.byref(dest))
    if not ok:
        raise SecurityError("无法解锁本机加密数据。请使用原来的 Windows 账号与电脑。")
    try:
        return ctypes.string_at(dest.pbData, dest.cbData)
    finally:
        kernel.LocalFree(dest.pbData)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Vault:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        key_file = self.path / "key.dpapi"
        if key_file.exists():
            key = protect(key_file.read_bytes(), decrypt=True)
        else:
            key = secrets.token_bytes(32)
            # Exclusive creation prevents two windows from replacing an existing key.
            temporary = self.path / (secrets.token_hex(12) + '.tmp')
            try:
                with temporary.open('xb') as file:
                    file.write(protect(key))
                    file.flush()
                    os.fsync(file.fileno())
                try:
                    # Windows rename fails if the destination exists and also
                    # works on exFAT, where hard links are unavailable.
                    if os.name == 'nt':
                        os.rename(temporary, key_file)
                    else:
                        # Android SELinux denies hard links in app storage.
                        # Serialize creators, then atomically publish a sealed
                        # key; never replace a key another process has created.
                        import fcntl
                        with key_file.with_suffix('.lock').open('a+b') as guard:
                            fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                            if key_file.exists():
                                key = protect(key_file.read_bytes(), decrypt=True)
                            else:
                                os.replace(temporary, key_file)
                except FileExistsError:
                    key = protect(key_file.read_bytes(), decrypt=True)
            finally:
                temporary.unlink(missing_ok=True)
        self.box = SecretBox(key)
        self.db_path = self.path / "history.sqlite3"
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS records(kind TEXT,key TEXT,value BLOB NOT NULL,PRIMARY KEY(kind,key));
            CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,cid TEXT NOT NULL,sender TEXT NOT NULL,client_id TEXT NOT NULL,value BLOB NOT NULL,UNIQUE(sender,client_id));
            CREATE INDEX IF NOT EXISTS history_cid ON history(cid,id);
            """)
        self.files = EncryptedFiles(self)

    def db(self):
        return sqlite3.connect(self.db_path, timeout=20, factory=ClosingConnection)

    def pack(self, kind, key, value):
        return bytes(self.box.encrypt(canonical({"kind": kind, "key": str(key), "value": value})))

    def unpack(self, kind, key, raw):
        data = json.loads(self.box.decrypt(raw))
        if data["kind"] != kind or data["key"] != str(key):
            raise SecurityError("本机加密记录校验失败。")
        return data["value"]

    def get(self, kind, key, default=None):
        with self.lock, self.db() as db:
            row = db.execute("SELECT value FROM records WHERE kind=? AND key=?", (kind, str(key))).fetchone()
            return self.unpack(kind, key, row[0]) if row else default

    def put(self, kind, key, value):
        with self.lock, self.db() as db:
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, str(key), self.pack(kind, key, value)))

    def delete(self, kind, key):
        with self.lock, self.db() as db:
            db.execute("DELETE FROM records WHERE kind=? AND key=?", (kind, str(key)))

    def preferences(self):
        with self.lock, self.db() as db:
            return {k: self.unpack("ui", k, v) for k, v in db.execute("SELECT key,value FROM records WHERE kind='ui'")}

    def save_message(self, message):
        with self.lock, self.db() as db:
            old = db.execute("SELECT id FROM history WHERE sender=? AND client_id=?", (message["sender"]["id"], message["client_id"])).fetchone()
            if old:
                return False
            db.execute("INSERT INTO history VALUES (?,?,?,?,?)", (message["id"], message["conversation_id"], message["sender"]["id"], message["client_id"], self.pack("message", message["id"], message)))
            return True

    def recall(self, target, sender, cid, created_at):
        """A durable tombstone prevents delayed delivery or history sync resurrection."""
        discarded = []
        with self.lock, self.db() as db:
            row = db.execute('SELECT id,value FROM history WHERE sender=? AND client_id=?', (sender['id'], target['client_id'])).fetchone()
            previous = self.unpack('message', row[0], row[1]) if row else None
            if previous and (previous['id'] != target['id'] or previous['conversation_id'] != cid):
                raise SecurityError('撤回消息与原记录不匹配。')
            conflict = db.execute('SELECT sender,client_id FROM history WHERE id=?', (target['id'],)).fetchone()
            if conflict and tuple(conflict) != (sender['id'], target['client_id']):
                raise SecurityError('撤回消息编号冲突。')
            message = {'id': target['id'], 'client_id': target['client_id'], 'conversation_id': cid,
                       'sender': sender, 'created_at': previous['created_at'] if previous else created_at,
                       'body': '消息已撤回', 'image': None, 'encrypted': True, 'recalled': True}
            db.execute('INSERT OR REPLACE INTO history VALUES (?,?,?,?,?)', (target['id'], cid, sender['id'], target['client_id'], self.pack('message', target['id'], message)))
            if previous:
                for kind, url in [('image', previous.get('image')), ('file', previous.get('attachment', {}).get('url'))]:
                    if url:
                        if kind == 'file':
                            row = db.execute('SELECT value FROM records WHERE kind=? AND key=?', (kind, url.rsplit('/', 1)[-1])).fetchone()
                            if row:
                                discarded.append((url.rsplit('/', 1)[-1], self.unpack(kind, url.rsplit('/', 1)[-1], row[0])))
                        db.execute('DELETE FROM records WHERE kind=? AND key=?', (kind, url.rsplit('/', 1)[-1]))
            saved = db.execute("SELECT value FROM records WHERE kind='ui' AND key='saved'").fetchone()
            if saved:
                values = self.unpack('ui', 'saved', saved[0])
                values = [m for m in values if m.get('id') != target['id']]
                db.execute("UPDATE records SET value=? WHERE kind='ui' AND key='saved'", (self.pack('ui', 'saved', values),))
        for asset, file in discarded:
            self.files.discard(asset, file)
        return message

    def message(self, sender, client_id):
        with self.lock, self.db() as db:
            row = db.execute("SELECT id,value FROM history WHERE sender=? AND client_id=?", (sender, client_id)).fetchone()
            return self.unpack("message", row[0], row[1]) if row else None

    def messages(self, cid=None, before=9223372036854775807, search=None, limit=51):
        with self.lock, self.db() as db:
            rows = db.execute("SELECT id,value FROM history WHERE id<?" + (" AND cid=?" if cid else "") + " ORDER BY id DESC", (before, cid) if cid else (before,))
            result = []
            for mid, value in rows:
                message = self.unpack("message", mid, value)
                if search is None or search.casefold() in message.get("body", "").casefold():
                    result.append(message)
                    if len(result) >= limit:
                        break
            return result

    def summary(self, conv, uid):
        cid = conv["id"]
        read = self.get("read", cid, 0)
        with self.lock, self.db() as db:
            count = sum(not self.unpack('message', mid, value).get('recalled') for mid, value in db.execute('SELECT id,value FROM history WHERE cid=? AND id>? AND sender!=?', (cid, read, uid)))
        last = self.messages(cid, limit=1)
        conv.update(last_message=last[0] if last else None, unread=count)
        return conv


class SecureClient:
    def __init__(self, profile, server_url, cookie, bootstrap):
        self.url, self.cookie = server_url, cookie
        self.me = bootstrap["me"]
        self.server_id = bootstrap["server_id"]
        identity = hashlib.sha256((self.server_id + ":" + self.me["id"]).encode()).hexdigest()
        self.vault = Vault(Path(profile) / "secure" / identity)
        self.lock = threading.RLock()
        self.receive_lock = threading.Lock()
        self.poll_time = 0
        self.sync_time = 0
        self.blocked = None
        keys = self.vault.get("identity", "keys")
        if keys is None:
            keys = {"device_id": secrets.token_hex(16), "box": b64(bytes(PrivateKey.generate())), "sign": b64(bytes(SigningKey.generate()))}
            self.vault.put("identity", "keys", keys)
        self.device_id = keys["device_id"]
        self.private = PrivateKey(unb64(keys["box"]))
        self.signing = SigningKey(unb64(keys["sign"]))
        if self.vault.get('trust', self.me['id']) is None:
            self.vault.put('trust', self.me['id'], {self.device_id: self.fingerprint({'box_key': b64(bytes(self.private.public_key)), 'sign_key': b64(bytes(self.signing.verify_key))})})
        device = {"device_id": self.device_id, "box_key": b64(bytes(self.private.public_key)), "sign_key": b64(bytes(self.signing.verify_key)), "name": "安卓客户端" if os.environ.get('WEIYAN_PLATFORM') == 'android' else "Windows 客户端"}
        proof = b64(self.signing.sign(DEVICE_DOMAIN + canonical(device)).signature)
        if not bootstrap.get('offline'):
            self.remote("/api/devices", {**device, "proof": proof})
        self.import_legacy(profile)
        self.transfers = FileTransfers(self)
        with self.vault.db() as db:
            pending_files = db.execute("SELECT 1 FROM records WHERE kind IN ('file_pending','control_job') LIMIT 1").fetchone()
        if pending_files:
            self.transfers.start()

    def remote_binary(self, path, data=None):
        parsed = urlsplit(self.url)
        cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        conn = cls(parsed.hostname, parsed.port, timeout=60)
        try:
            conn.request('POST' if data is not None else 'GET', path, body=data,
                         headers={'Cookie': self.cookie, 'Origin': self.url, 'X-Weiyan-Client': 'web', 'Content-Type': 'application/octet-stream'})
            response = conn.getresponse()
            raw = response.read(CHUNK_SIZE + OVERHEAD + 1)
            if response.status >= 400:
                try: message = json.loads(raw).get('error', '附件传输未完成。')
                except ValueError: message = '附件传输未完成。'
                raise RemoteError(message, response.status)
            if data is None:
                if len(raw) > CHUNK_SIZE + OVERHEAD: raise SecurityError('附件分片过大。')
                return raw
            return json.loads(raw)
        except (OSError, http.client.HTTPException) as error:
            raise RemoteError('附件传输已暂停，网络恢复后继续。', 503) from error
        finally:
            conn.close()

    def prepare_files(self, clear, use_prepared=False):
        clear = copy.deepcopy(clear)
        files = []
        containers = [clear] + (clear.get('entries') or [])
        for container in containers:
            file = container.get('attachment')
            if file and file.get('blob'):
                prepared = self.vault.files.prepare(file, use_prepared)
                container['attachment'] = prepared
                files.append(prepared)
        return clear, files

    def acknowledge(self, mid, messages=()):
        if self.transfers.pending(mid):
            return
        assets = []
        for message in messages:
            if message.get('recalled'): continue
            url = message.get('attachment', {}).get('url')
            file = self.vault.get('file', url.rsplit('/', 1)[-1]) if url else None
            if file and file.get('blob') and not self.vault.files.ready(file): assets.append(url.rsplit('/', 1)[-1])
        if not self.transfers.defer(mid, assets):
            self.remote('/api/received', {'device_id': self.device_id, 'id': mid})

    @staticmethod
    def check_blob_references(packet, clear):
        if not isinstance(clear, dict):
            raise SecurityError('加密消息格式无效。')
        entries = clear.get('entries', [])
        if not isinstance(entries, list) or len(entries) > 8 or any(not isinstance(entry, dict) for entry in entries):
            raise SecurityError('加密附件列表无效。')
        containers = [clear] + entries
        references = []
        for container in containers:
            file = container.get('attachment')
            if file is not None and not isinstance(file, dict):
                raise SecurityError('加密附件格式无效。')
            if file and file.get('blob'):
                file = checked_descriptor(file)
                transfer = file['blob'].get('transfer')
                if not transfer:
                    raise SecurityError('附件缺少签名传输编号。')
                references.append(transfer)
        declared = packet.get('blobs', [])
        if not isinstance(declared, list) or any(not isinstance(b, str) for b in declared) or len(set(declared)) != len(declared) or sorted(references) != sorted(declared):
            raise SecurityError('附件引用与签名消息不一致。')

    def remote(self, path, data=None):
        parsed = urlsplit(self.url)
        cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = cls(parsed.hostname, parsed.port, timeout=20)
        try:
            conn.request("POST" if data is not None else "GET", path, body=canonical(data) if data is not None else None,
                         headers={"Cookie": self.cookie, "Origin": self.url, "X-Weiyan-Client": "web", "Content-Type": "application/json"})
            response = conn.getresponse()
            value = json.loads(response.read())
            if response.status >= 400:
                raise RemoteError(value.get("error", "服务端暂时无法响应。"), response.status)
            return value
        except (OSError, http.client.HTTPException) as error:
            raise RemoteError("连接暂时断开了，小笺已留在本机。", 503) from error
        finally:
            conn.close()

    @staticmethod
    def fingerprint(device):
        digest = hashlib.sha256(unb64(device["box_key"]) + unb64(device["sign_key"])).hexdigest().upper()
        return " ".join(digest[i:i+4] for i in range(0, len(digest), 4))

    def directory(self, cid, approve=False, expected=None, receiving=False, recall=None):
        suffix = '&receiving=1&device_id=' + self.device_id if receiving else ''
        if recall:
            suffix = '&recall_id=' + str(recall['id']) + '&recall_client_id=' + quote(recall['client_id'])
        directory = self.remote("/api/keys?conversation_id=" + quote(cid) + suffix)["devices"]
        groups = {}
        for device in directory:
            groups.setdefault(device["user_id"], {})[device["device_id"]] = self.fingerprint(device)
        digest = hashlib.sha256(canonical(groups)).hexdigest()
        if approve and expected != digest:
            raise SecurityError("设备列表刚刚发生变化，请重新核对后确认。")
        changed = []
        for uid, current in groups.items():
            pinned = self.vault.get("trust", uid)
            if pinned is None:
                self.vault.put("trust", uid, current)
            elif any(pinned.get(did) != fingerprint for did, fingerprint in current.items()):
                changed.append(uid)
                if approve:
                    self.vault.put("trust", uid, current)
        self.blocked = {"conversation_id": cid, "users": changed} if changed and not approve else None
        return directory, changed if not approve else [], digest

    def security(self, cid=None, approve=False, expected=None):
        directory, changed, digest = self.directory(cid, approve, expected, receiving=True) if cid else ([], [], "")
        policy = self.remote("/api/health").get("privacy", {})
        return {"encrypted": True, "local_encrypted": True, "algorithm": "libsodium · XSalsa20-Poly1305 / X25519 / Ed25519", "device_id": self.device_id,
                "fingerprint": self.fingerprint({"box_key": b64(bytes(self.private.public_key)), "sign_key": b64(bytes(self.signing.verify_key))}),
                "devices": [{**d, "fingerprint": self.fingerprint(d), "changed": d["user_id"] in changed} for d in directory],
                "key_changed": bool(changed), "directory_digest": digest, "privacy": policy}

    def packet(self, cid, client_id, clear, directory, **extra):
        key = secrets.token_bytes(32)
        packet = {'v': 2, 'conversation_id': cid, 'sender_id': self.me['id'], 'device_id': self.device_id,
                  'client_id': client_id, 'ciphertext': b64(bytes(SecretBox(key).encrypt(canonical(clear)))),
                  'keys': {d['device_id']: b64(SealedBox(PublicKey(unb64(d['box_key']))).encrypt(key)) for d in directory}, **extra}
        packet['signature'] = b64(self.signing.sign(DOMAIN + canonical(packet)).signature)
        return packet

    def control_send(self, cid, client_id, clear, directory, **extra):
        # Persist the exact signed envelope across ambiguous network failures.
        with self.lock:
            done = self.vault.get('control_done', client_id)
            if done:
                return done
            packet = self.vault.get('control_pending', client_id)
            if packet is None:
                clear, files = self.prepare_files(clear)
                if files: extra['blobs'] = [file['blob']['transfer'] for file in files]
                packet = self.packet(cid, client_id, clear, directory, **extra)
                self.vault.put('control_files', client_id, files)
                self.vault.put('control_pending', client_id, packet)
        try:
            for file in self.vault.get('control_files', client_id, []): self.vault.files.upload(self, file)
            result = self.remote('/api/relay', {'envelope': packet})
        except RemoteError as error:
            if error.status == 409 and '设备列表' in str(error):
                self.vault.delete('control_pending', client_id)
            raise
        with self.lock:
            self.vault.put('control_done', client_id, result)
            self.vault.delete('control_pending', client_id)
            self.vault.delete('control_files', client_id)
            return result

    def recall(self, data):
        message = self.vault.message(self.me['id'], data.get('client_id'))
        if not message or message['id'] != data.get('id'):
            raise SecurityError('只能撤回自己发送的消息。')
        if message.get('recalled'):
            return message
        target = {'id': message['id'], 'client_id': message['client_id']}
        cid = message['conversation_id']
        control_id = 'recall-' + str(message['id'])
        directory, changed = [], []
        if not self.vault.get('control_pending', control_id) and not self.vault.get('control_done', control_id):
            directory, changed, _ = self.directory(cid, recall=target)
            if changed:
                raise SecurityError('设备发生变化，请先核对安全指纹。')
        clear = {'type': 'recall', 'created_at': message['created_at'], 'sender': message['sender']}
        self.control_send(cid, control_id, clear, directory, recall_of=target)
        return self.vault.recall(target, message['sender'], cid, message['created_at'])

    def sync_tick(self):
        if time.monotonic() - self.sync_time < 25:
            return
        self.sync_time = time.monotonic()
        cid = 'sync:' + self.me['id']
        directory, changed, _ = self.directory(cid)
        if changed:
            return
        for peer in directory:
            did = peer['device_id']
            if did == self.device_id:
                continue
            progress = self.vault.get('sync_progress', did, {'after': 0, 'done': False})
            if not progress['done']:
                clear = {'type': 'sync_request', 'after': progress['after'], 'until': progress.get('until')}
                targets = [d for d in directory if d['device_id'] in (did, self.device_id)]
                request = self.vault.get('sync_request', did)
                if request is None or request['after'] != progress['after'] or request.get('until') != progress.get('until'):
                    request = {**clear, 'client_id': 'sync-' + secrets.token_hex(16)}
                    self.vault.put('sync_request', did, request)
                self.control_send(cid, request['client_id'], clear, targets, sync_to=did)

    def sync_control(self, packet, clear, directory):
        if packet['sender_id'] != self.me['id'] or packet.get('sync_to') != self.device_id or packet['conversation_id'] != 'sync:' + self.me['id']:
            raise SecurityError('设备同步身份不正确。')
        peer = packet['device_id']
        if clear.get('type') == 'sync_request':
            after = clear.get('after')
            if type(after) is not int or after < 0:
                raise SecurityError('同步进度无效。')
            until = clear.get('until')
            if until is not None and (type(until) is not int or until < after or until > 9223372036854775807):
                raise SecurityError('同步范围无效。')
            with self.vault.lock, self.vault.db() as db:
                if until is None:
                    until = db.execute('SELECT COALESCE(MAX(id),0) FROM history').fetchone()[0]
                rows = list(db.execute('''SELECT id,value FROM
                    (SELECT id,value,ROW_NUMBER() OVER(PARTITION BY cid ORDER BY id DESC) AS position FROM history WHERE id<=?)
                    WHERE position<=50 AND id>? ORDER BY id LIMIT 8''', (until, after)))
            entries, size = [], 0
            for mid, raw in rows:
                message = self.vault.unpack('message', mid, raw)
                entry = {'message': message}
                if message.get('image'):
                    entry['image'] = self.vault.get('image', message['image'].rsplit('/', 1)[-1])
                if message.get('attachment'):
                    entry['attachment'] = self.vault.get('file', message['attachment']['url'].rsplit('/', 1)[-1])
                length = len(canonical(entry))
                if entries and size + length > 28 * 1024 * 1024:
                    break
                entries.append(entry)
                size += length
            response = {'type': 'sync_batch', 'request_id': packet['client_id'], 'after': after, 'until': until, 'entries': entries,
                        'done': len(entries) < 8 and len(entries) == len(rows)}
            targets = [d for d in directory if d['device_id'] in (peer, self.device_id)]
            batch_id = 'batch-' + hashlib.sha256(packet['client_id'].encode()).hexdigest()
            if any(entry.get('attachment', {}).get('blob') for entry in entries):
                self.vault.put('control_job', batch_id, {'cid': packet['conversation_id'], 'clear': response, 'directory': targets, 'extra': {'sync_to': peer}})
                self.transfers.start()
            else:
                self.control_send(packet['conversation_id'], batch_id, response, targets, sync_to=peer)
            return []
        if clear.get('type') != 'sync_batch' or not isinstance(clear.get('entries'), list) or len(clear['entries']) > 8:
            raise SecurityError('同步内容无效。')
        progress = self.vault.get('sync_progress', peer, {'after': 0, 'done': False})
        if type(clear.get('after')) is not int or clear['after'] > progress['after']:
            raise SecurityError('同步进度不连续。')
        until = clear.get('until')
        if progress.get('until') is not None and progress['until'] != until and clear['after'] <= progress['after']:
            # A retried initial request can race the first accepted snapshot.
            return []
        if type(until) is not int or until < clear['after'] or until > 9223372036854775807 or (progress.get('until') is not None and progress['until'] != until):
            raise SecurityError('同步范围不连续。')
        result, cursor = [], clear['after']
        for entry in clear['entries']:
            message = entry['message']
            if type(message.get('id')) is not int or not cursor < message['id'] <= until or not isinstance(message.get('conversation_id'), str) or message['conversation_id'].startswith('sync:'):
                raise SecurityError('同步记录编号无效。')
            cursor = message['id']
            old = self.vault.message(message['sender']['id'], message['client_id'])
            if message.get('recalled'):
                stored = self.vault.recall({'id': message['id'], 'client_id': message['client_id']}, message['sender'], message['conversation_id'], message['created_at'])
                result.append({**stored, 'history_sync': True})
            elif not old:
                with self.vault.lock:
                    if self.vault.message(message['sender']['id'], message['client_id']):
                        continue
                    stored = self.store_clear(message['id'], message['conversation_id'], message['client_id'],
                                              {'body': message['body'], 'sender': message['sender'], 'created_at': message['created_at'],
                                               'image': entry.get('image'), 'attachment': entry.get('attachment')})
                    saved = self.vault.save_message(stored)
                if saved:
                    result.append({**stored, 'history_sync': True})
                    cid = stored['conversation_id']
                    self.vault.put('read', cid, max(self.vault.get('read', cid, 0), stored['id']))
        if cursor >= progress['after']:
            self.vault.put('sync_progress', peer, {'after': cursor, 'until': until, 'done': bool(clear.get('done'))})
        request = self.vault.get('sync_request', peer)
        if request and request['client_id'] == clear.get('request_id'):
            self.vault.delete('sync_request', peer)
        self.sync_time = 0
        return result

    def store_clear(self, mid, cid, client_id, clear):
        if not isinstance(clear['body'], str) or len(clear['body']) > 2000:
            raise SecurityError('消息正文无效。')
        image_url, attachment = None, None
        asset_id = hashlib.sha256((clear['sender']['id'] + ':' + client_id).encode()).hexdigest()
        if clear.get('image'):
            self.vault.put('image', asset_id, validate_image(clear['image']))
            image_url = '/local-images/' + asset_id
        if clear.get('attachment'):
            if clear['attachment'].get('blob'):
                attachment = self.vault.files.import_file(asset_id, clear['attachment'])
            else:
                file = validate_file(clear['attachment'])
                self.vault.put('file', asset_id, file)
                attachment = {k: v for k, v in file.items() if k != 'data'}
                attachment['url'] = '/local-files/' + asset_id
        message = {'id': mid, 'client_id': client_id, 'conversation_id': cid, 'body': clear['body'],
                   'sender': clear['sender'], 'created_at': clear['created_at'], 'image': image_url, 'encrypted': True}
        if attachment:
            message['attachment'] = attachment
        return message

    def send(self, data):
        with self.lock:
            cid, client_id = data.get("conversation_id"), data.get("client_id")
            if not isinstance(cid, str) or not isinstance(client_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", client_id):
                raise ValueError("消息编号无效。")
            existing = self.vault.message(self.me["id"], client_id)
            if existing:
                if existing["conversation_id"] != cid:
                    raise ValueError("消息编号与会话不匹配。")
                return existing
            body = data.get("body", "")
            if not isinstance(body, str) or len(body) > 2000:
                raise ValueError("消息最多 2000 个字符。")
            image_id = data.get("image_id")
            image = self.vault.get("image", image_id) if image_id else None
            file_id = data.get('file_id')
            attachment = self.vault.get('file', file_id) if file_id else None
            if file_id and not attachment:
                raise ValueError('文件无法发送，请重新选择。')
            if image and attachment:
                raise ValueError('每条消息请选择一项附件。')
            if image_id and not image:
                raise ValueError("图片无法发送，请重新选择。")
            if not body.strip() and not image and not attachment:
                raise ValueError("写点什么，再寄出这封小笺吧。")
            directory, changed, _ = self.directory(cid)
            if changed:
                raise SecurityError("联系人设备发生变化，发送已暂停。请点击加密标识核对安全指纹。")
            if cid.startswith("dm:") and not any(d["user_id"] != self.me["id"] for d in directory):
                raise SecurityError("对方尚未建立加密设备，请让对方先登录客户端。")
            pending = self.vault.get("pending", client_id)
            if pending and pending["packet"]["conversation_id"] != cid:
                raise ValueError("消息编号与会话不匹配。")
            if pending is None:
                created = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"
                clear = {"body": body, "image": image, "sender": self.me, "created_at": created}
                if attachment:
                    clear['attachment'] = attachment
                if cid.startswith('group:'):
                    group = self.remote('/api/groups?conversation_id=' + quote(cid))['conversation']['group']
                    if group['nickname']:
                        clear['sender'] = {**self.me, 'nickname': group['nickname']}
                clear, files = self.prepare_files(clear, use_prepared=True)
                key = secrets.token_bytes(32)
                packet = {"v": 2, "conversation_id": cid, "sender_id": self.me["id"], "device_id": self.device_id, "client_id": client_id,
                          "ciphertext": b64(bytes(SecretBox(key).encrypt(canonical(clear)))),
                          "keys": {d["device_id"]: b64(SealedBox(PublicKey(unb64(d["box_key"]))).encrypt(key)) for d in directory}}
                if files: packet['blobs'] = [file['blob']['transfer'] for file in files]
                packet["signature"] = b64(self.signing.sign(DOMAIN + canonical(packet)).signature)
                pending = {"packet": packet, "clear": clear}
                self.vault.put("pending", client_id, pending)
            try:
                response = self.remote("/api/relay", {"envelope": pending["packet"]})
            except RemoteError as error:
                if error.status == 409 and '设备列表' in str(error):
                    self.vault.delete('pending', client_id)
                raise
            clear = pending["clear"]
            if clear.get('attachment', {}).get('blob'):
                self.vault.put('blob_uploaded', clear['attachment']['blob']['transfer'], 'used')
                self.vault.delete('file_upload', file_id)
            message = {"id": response["id"], "conversation_id": cid, "client_id": client_id, "body": clear["body"], "sender": clear["sender"],
                       "created_at": clear["created_at"], "image": f"/local-images/{image_id}" if image_id else None, "encrypted": True}
            if clear.get('attachment'):
                message['attachment'] = {k: v for k, v in clear['attachment'].items() if k not in ('data', 'blob')}
                message['attachment']['url'] = '/local-files/' + file_id
                if clear['attachment'].get('blob'):
                    message['attachment'].update(streamed=True, ready=True)
            self.vault.save_message(message)
            self.vault.delete("pending", client_id)
            return message

    def receive(self):
        with self.receive_lock:
            result, after, cache = [], 0, {}
            started = time.monotonic()
            for _ in range(32):
                messages, after, count = self._receive_page(after, cache)
                result.extend(messages)
                if count < 12 or time.monotonic() - started > 2:
                    break
            try:
                self.sync_tick()
            except RemoteError as error:
                if error.status not in (403, 404, 503):
                    raise
            return result

    def _receive_page(self, after, cache):
            result = []
            started, last, processed = time.monotonic(), after, 0
            inbox = self.remote("/api/inbox?device_id=" + self.device_id + "&after=" + str(after))
            for item in inbox["items"]:
                if processed and time.monotonic() - started > 1:
                    break
                last, processed = item['id'], processed + 1
                packet = item["envelope"]
                cid = packet["conversation_id"]
                try:
                    if self.vault.get('control_seen', str(item['id'])):
                        self.acknowledge(item['id'])
                        continue
                    old = self.vault.message(packet["sender_id"], packet["client_id"])
                    if old:
                        self.acknowledge(item['id'], [old])
                        continue
                    if cid not in cache:
                        cache[cid] = self.directory(cid, receiving=True)
                    directory, changed, _ = cache[cid]
                    if packet["sender_id"] in changed:
                        raise SecurityError("联系人设备发生变化，请核对安全指纹。")
                    sender_device = next((d for d in directory if d["device_id"] == packet["device_id"] and d["user_id"] == packet["sender_id"]), None)
                    if not sender_device:
                        raise SecurityError("发送设备已撤销，无法验证这条消息。")
                    signed = {k: v for k, v in packet.items() if k != "signature"}
                    VerifyKey(unb64(sender_device["sign_key"])).verify(DOMAIN + canonical(signed), unb64(packet["signature"]))
                    key = SealedBox(self.private).decrypt(unb64(packet["keys"][self.device_id]))
                    clear = json.loads(SecretBox(key).decrypt(unb64(packet["ciphertext"])))
                    self.check_blob_references(packet, clear)
                    if 'sync_to' in packet:
                        synchronized = self.sync_control(packet, clear, directory)
                        result.extend(synchronized)
                        # Persist deferred attachment receipts before marking the envelope seen.
                        references = []
                        for entry in clear.get('entries', []):
                            reference = entry.get('message', {})
                            if reference.get('sender') and reference.get('client_id'):
                                stored = self.vault.message(reference['sender']['id'], reference['client_id'])
                                if stored: references.append(stored)
                        self.acknowledge(item['id'], references)
                        self.vault.put('control_seen', str(item['id']), True)
                        continue
                    if 'recall_of' in packet:
                        if clear.get('type') != 'recall' or clear['sender']['id'] != packet['sender_id']:
                            raise SecurityError('撤回消息签名身份无效。')
                        message = self.vault.recall(packet['recall_of'], clear['sender'], cid, clear['created_at'])
                        self.vault.put('control_seen', str(item['id']), True)
                        result.append(message)
                        self.remote('/api/received', {'device_id': self.device_id, 'id': item['id']})
                        continue
                    if clear["sender"]["id"] != packet["sender_id"] or not isinstance(clear["body"], str) or len(clear["body"]) > 2000:
                        raise SecurityError("消息身份校验失败。")
                    with self.vault.lock:
                        # Recall may have committed after the first duplicate check.
                        if self.vault.message(packet['sender_id'], packet['client_id']):
                            self.acknowledge(item['id'], [self.vault.message(packet['sender_id'], packet['client_id'])])
                            continue
                        message = self.store_clear(item['id'], cid, packet['client_id'], clear)
                        saved = self.vault.save_message(message)
                    if saved:
                        result.append(message)
                        callback = getattr(self, 'on_received', None)
                        if callback:
                            callback(message)
                    # Acknowledge only after the encrypted SQLite transaction commits.
                    self.acknowledge(item['id'], [message])
                except (CryptoError, BadSignatureError, KeyError, TypeError, ValueError) as error:
                    self.blocked = {"conversation_id": cid, "error": str(error)}
                    # Keep the ciphertext queued; an unverified message never enters history.
            return result, last, processed

    def bootstrap(self, data):
        self.me = data["me"]
        try:
            self.receive()
        except RemoteError as error:
            if error.status != 503:
                raise
        data["conversations"] = [self.vault.summary(conv, self.me["id"]) for conv in data["conversations"]]
        data["local_storage"] = self.vault.preferences()
        data["security"] = {"encrypted": True, "local_encrypted": True, "blocked": self.blocked}
        return data

    def import_legacy(self, profile):
        default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WeiyanPoetry" / "client" / "legacy"
        for folder in {Path(profile) / "legacy", default}:
            if not folder.exists():
                continue
            for file in folder.glob("*.dpapi"):
                marker = hashlib.sha256(file.read_bytes()).hexdigest()
                if self.vault.get("legacy", marker):
                    continue
                archive = json.loads(protect(file.read_bytes(), decrypt=True))
                if archive["server_id"] != self.server_id:
                    continue
                allowed = set(archive["members"].get(self.me["id"], []))
                for message in archive["messages"]:
                    if not message["conversation_id"].startswith("room:") and message["conversation_id"] not in allowed:
                        continue
                    message = dict(message)
                    if message.get("image"):
                        image_id = message["image"].rsplit("/", 1)[-1]
                        if image_id in archive["images"]:
                            self.vault.put("image", image_id, archive["images"][image_id])
                            message["image"] = "/local-images/" + image_id
                        else:
                            message["image"] = None
                    message["encrypted"] = True
                    self.vault.save_message(message)
                self.vault.put("legacy", marker, True)
