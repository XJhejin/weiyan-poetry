"""Bounded-memory attachment storage and transfer. Only ciphertext is persisted."""
import base64
import copy
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import sqlite3
from urllib.parse import quote

from nacl.secret import SecretBox
from nacl.exceptions import CryptoError

MAX_SIZE = 10 * 1024 ** 3
CHUNK_SIZE = 1024 ** 2
MAGIC = b'WYFILE1\0'
OVERHEAD = 88


def checked_file(value):
    if not isinstance(value, dict):
        raise ValueError('附件格式不正确。')
    name, size, kind = value.get('name'), value.get('size'), value.get('kind')
    if not isinstance(name, str) or not 1 <= len(name) <= 180 or any(ord(c) < 32 or c in '/\\' for c in name):
        raise ValueError('文件名无效。')
    if type(size) is not int or not 0 <= size <= MAX_SIZE:
        raise ValueError('请选择 10 GB 以内的附件。')
    if kind not in ('image', 'video', 'file'):
        raise ValueError('附件类型无效。')
    return {'name': name, 'size': size, 'kind': kind, 'mime': 'application/octet-stream'}


def checked_descriptor(value):
    result = checked_file(value)
    blob = value.get('blob')
    if not isinstance(blob, dict) or blob.get('format') != 1 or not isinstance(blob.get('id'), str) or not re.fullmatch(r'[0-9a-f]{32}', blob['id']):
        raise ValueError('加密附件描述无效。')
    key = base64.b64decode(blob.get('key', ''), validate=True)
    if len(key) != 32 or blob.get('chunk_size') != CHUNK_SIZE:
        raise ValueError('加密附件密钥或分片大小无效。')
    transfer = blob.get('transfer')
    if transfer is not None and (not isinstance(transfer, str) or not re.fullmatch(r'[0-9a-f]{32}', transfer)):
        raise ValueError('附件传输编号无效。')
    allowed = {'file': ('application/octet-stream',), 'video': ('video/mp4', 'video/webm'),
               'image': ('image/png', 'image/jpeg', 'image/gif', 'image/webp')}
    if value.get('mime') not in allowed[result['kind']]:
        raise ValueError('附件预览格式无效。')
    result['mime'] = value['mime']
    result['blob'] = {'format': 1, 'id': blob['id'], 'key': blob['key'], 'chunk_size': CHUNK_SIZE}
    if transfer is not None:
        result['blob']['transfer'] = transfer
    return result


class EncryptedFiles:
    def __init__(self, vault):
        self.vault = vault
        self.root = vault.path / 'attachments'
        self.root.mkdir(exist_ok=True)
        self.uploaded_parts = {}

    @staticmethod
    def count(file):
        return max(1, (file['size'] + CHUNK_SIZE - 1) // CHUNK_SIZE)

    def part_path(self, file, index):
        bid = file['blob']['id']
        if not re.fullmatch(r'[0-9a-f]{32}', bid) or type(index) is not int or not 0 <= index < self.count(file):
            raise ValueError('附件分片编号无效。')
        return self.root / bid / f'{index:08x}.part'

    def begin(self, payload):
        file = checked_file(payload)
        bid = secrets.token_hex(16)
        file['blob'] = {'format': 1, 'id': bid, 'key': base64.b64encode(secrets.token_bytes(32)).decode(), 'chunk_size': CHUNK_SIZE}
        self.vault.put('file', bid, file)
        self.vault.put('blob_identity', bid, {'key': file['blob']['key'], 'size': file['size']})
        self.vault.put('file_upload', bid, {k: file[k] for k in ('name', 'size', 'kind')})
        return {'id': bid, 'chunk_size': CHUNK_SIZE, 'parts': self.count(file)}

    def resumable(self, payload):
        metadata = {k: payload.get(k) for k in ('name', 'size', 'kind')}
        with self.vault.lock, self.vault.db() as db:
            for asset, sealed in db.execute("SELECT key,value FROM records WHERE kind='file_upload' ORDER BY rowid DESC"):
                if self.vault.unpack('file_upload', asset, sealed) == metadata:
                    return asset

    def decode(self, file, index, ciphertext):
        prefix = MAGIC + file['blob']['id'].encode('ascii') + index.to_bytes(8, 'big')
        expected = min(CHUNK_SIZE, file['size'] - index * CHUNK_SIZE)
        if len(ciphertext) != expected + OVERHEAD:
            raise ValueError('附件分片长度校验失败。')
        clear = SecretBox(base64.b64decode(file['blob']['key'], validate=True)).decrypt(ciphertext)
        if not clear.startswith(prefix) or len(clear) != len(prefix) + expected:
            raise ValueError('附件分片身份或顺序校验失败。')
        return clear[len(prefix):]

    def write_cipher(self, file, index, ciphertext):
        self.decode(file, index, ciphertext)
        path = self.part_path(file, index)
        new_part = not path.exists()
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if new_part:
                bid = file['blob']['id']
                self.vault.put('blob_bytes', bid, self.vault.get('blob_bytes', bid, 0) + len(ciphertext) - OVERHEAD)
        finally:
            temporary.unlink(missing_ok=True)

    def put(self, asset, index, raw):
        with self.vault.lock:
            file = self.vault.get('file', asset)
            if not file or not file.get('blob'):
                raise ValueError('附件准备记录不存在。')
            path = self.part_path(file, index)
            expected = min(CHUNK_SIZE, file['size'] - index * CHUNK_SIZE)
            if len(raw) != expected:
                raise ValueError('附件分片长度无效。')
            if path.exists():
                if self.decode(file, index, path.read_bytes()) != raw:
                    raise ValueError('附件分片与已保存内容不一致。')
                return
            prefix = MAGIC + file['blob']['id'].encode('ascii') + index.to_bytes(8, 'big')
            cipher = bytes(SecretBox(base64.b64decode(file['blob']['key'])).encrypt(prefix + raw))
            self.write_cipher(file, index, cipher)

    def finish(self, asset):
        with self.vault.lock:
            file = self.vault.get('file', asset)
            if not file or not file.get('blob'):
                raise ValueError('附件准备记录不存在。')
            for index in range(self.count(file)):
                expected = min(CHUNK_SIZE, file['size'] - index * CHUNK_SIZE) + OVERHEAD
                if not self.part_path(file, index).is_file() or self.part_path(file, index).stat().st_size != expected:
                    raise ValueError('附件尚未准备完整。')
            head = self.decode(file, 0, self.part_path(file, 0).read_bytes())[:64]
            if file['kind'] == 'image':
                if head.startswith(b'\x89PNG\r\n\x1a\n'): file['mime'] = 'image/png'
                elif head.startswith(b'\xff\xd8\xff'): file['mime'] = 'image/jpeg'
                elif head.startswith((b'GIF87a', b'GIF89a')): file['mime'] = 'image/gif'
                elif len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP': file['mime'] = 'image/webp'
                else: raise ValueError('支持 PNG、JPG、GIF 和 WebP 图片。')
            elif file['kind'] == 'video':
                if len(head) >= 12 and head[4:8] == b'ftyp': file['mime'] = 'video/mp4'
                elif head.startswith(b'\x1aE\xdf\xa3'): file['mime'] = 'video/webm'
                else: raise ValueError('视频预览支持 MP4 和 WebM 格式，请将其他格式作为普通文件发送。')
            self.vault.put('file', asset, file)
            self.vault.put('blob_ready', file['blob']['id'], True)
            return {k: v for k, v in file.items() if k not in ('blob', 'data')} | {'id': asset, 'url': '/local-files/' + asset, 'streamed': True, 'ready': True}

    def ready(self, file):
        return bool(self.vault.get('blob_ready', file['blob']['id']))

    def import_file(self, asset, value):
        file = checked_descriptor(value)
        identity = {'key': file['blob']['key'], 'size': file['size']}
        previous = self.vault.get('blob_identity', file['blob']['id'])
        if previous is not None and previous != identity:
            raise ValueError('附件编号与本机加密资料冲突。')
        self.vault.put('blob_identity', file['blob']['id'], identity)
        self.vault.put('file', asset, file)
        return {k: v for k, v in file.items() if k != 'blob'} | {'url': '/local-files/' + asset, 'streamed': True, 'ready': self.ready(file)}

    def read_range(self, file, start, end):
        for index in range(start // CHUNK_SIZE, end // CHUNK_SIZE + 1):
            raw = self.decode(file, index, self.part_path(file, index).read_bytes())
            left, right = max(0, start-index*CHUNK_SIZE), min(len(raw), end-index*CHUNK_SIZE+1)
            yield raw[left:right]

    def prepare(self, file, use_prepared=False):
        file = copy.deepcopy(checked_descriptor(file))
        if not self.ready(file):
            raise ValueError('附件尚未接收完整，请稍后再试。')
        existing = file['blob'].get('transfer')
        if use_prepared:
            if existing and self.vault.get('blob_uploaded', existing) == 'ready':
                return file
            raise ValueError('附件需要重新准备，请重新选择文件。')
        file['blob']['transfer'] = secrets.token_hex(16)
        return file

    def reserve(self, context, asset):
        file = self.vault.get('file', asset)
        if not file or not file.get('blob'):
            raise ValueError('附件准备记录不存在。')
        if self.vault.get('blob_uploaded', file['blob'].get('transfer')) == 'used':
            raise ValueError('该附件已经发送，请重新选择文件。')
        file['blob'].setdefault('transfer', secrets.token_hex(16))
        self.vault.put('file', asset, file)
        state = context.remote('/api/blobs', {'id': file['blob']['transfer'], 'device_id': context.device_id, 'size': file['size']})
        self.uploaded_parts[file['blob']['transfer']] = set(state['parts'])

    def send_part(self, context, asset, index):
        file = self.vault.get('file', asset)
        transfer = file['blob']['transfer']
        if index not in self.uploaded_parts.get(transfer, set()):
            context.remote_binary('/api/blobs/' + transfer + '/' + str(index), self.part_path(file, index).read_bytes())
            self.uploaded_parts.setdefault(transfer, set()).add(index)

    def uploaded(self, context, asset):
        file = self.vault.get('file', asset)
        state = context.remote('/api/blobs', {'id': file['blob']['transfer'], 'device_id': context.device_id, 'size': file['size']})
        if state['parts'] != list(range(self.count(file))):
            self.uploaded_parts[file['blob']['transfer']] = set(state['parts'])
            raise ValueError('附件尚未完整上传，请重新选择后重试。')
        self.vault.put('blob_uploaded', file['blob']['transfer'], 'ready')

    def upload(self, context, file):
        blob = file['blob']
        if self.vault.get('blob_uploaded', blob['transfer']) in ('ready', 'used'):
            return
        state = context.remote('/api/blobs', {'id': blob['transfer'], 'device_id': context.device_id, 'size': file['size']})
        received = set(state['parts'])
        for index in range(self.count(file)):
            if context.transfers.stopped.is_set(): raise OSError('附件传输已停止。')
            if index not in received:
                context.remote_binary('/api/blobs/' + blob['transfer'] + '/' + str(index), self.part_path(file, index).read_bytes())
        self.vault.put('blob_uploaded', blob['transfer'], 'ready')

    def status(self, asset):
        file = self.vault.get('file', asset)
        if not file:
            return {'ready': False, 'missing': True}
        if not file.get('blob'):
            return {'ready': True, 'received': file.get('size', 0), 'size': file.get('size', 0)}
        ready = self.ready(file)
        received = file['size'] if ready else self.vault.get('blob_bytes', file['blob']['id'], 0)
        return {'ready': ready, 'received': received, 'size': file['size']}

    def discard(self, asset, previous=None):
        with self.vault.lock:
            file = self.vault.get('file', asset) or previous
            self.vault.delete('file', asset)
            self.vault.delete('file_upload', asset)
            if not file or not file.get('blob'):
                return
            bid = file['blob']['id']
            with self.vault.db() as db:
                for key, value in db.execute("SELECT key,value FROM records WHERE kind='file'"):
                    if self.vault.unpack('file', key, value).get('blob', {}).get('id') == bid:
                        return
            folder = self.root / bid
            if re.fullmatch(r'[0-9a-f]{32}', bid) and folder.resolve().is_relative_to(self.root.resolve()) and folder.is_dir():
                shutil.rmtree(folder)
            self.vault.delete('blob_ready', bid)
            self.vault.delete('blob_bytes', bid)


class FileTransfers:
    def __init__(self, context):
        self.context = context
        self.lock = threading.Lock()
        self.running = False
        self.wake = threading.Event()
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()
        self.wake.set()

    def defer(self, mid, assets):
        assets = list(dict.fromkeys(assets))
        if not assets:
            return False
        self.context.vault.put('file_pending', str(mid), {'assets': assets})
        self.start()
        return True

    def start(self):
        if self.stopped.is_set(): return
        self.wake.set()
        with self.lock:
            if self.running: return
            self.running = True
            threading.Thread(target=self.run, daemon=True, name='encrypted-attachment-transfer').start()

    def pending(self, mid):
        if not self.context.vault.get('file_pending', str(mid)):
            return False
        self.start()
        return True

    def run(self):
        context = self.context
        try:
            while not self.stopped.is_set():
                self.wake.clear()
                if os.environ.get('WEIYAN_PLATFORM') == 'android':
                    from java import jclass
                    if not jclass('com.weiyan.poetry.MessageNotifications').isEnabled() and not jclass('com.weiyan.poetry.MainActivity').isForeground:
                        self.wake.wait(2)
                        continue
                with context.vault.db() as db:
                    records = list(db.execute("SELECT key,value FROM records WHERE kind='file_pending'"))
                    jobs = list(db.execute("SELECT key,value FROM records WHERE kind='control_job'"))
                if not records and not jobs: return
                failed = False
                for mid, sealed in records:
                    progress = context.vault.unpack('file_pending', mid, sealed)
                    try:
                        for asset in progress['assets']:
                            file = context.vault.get('file', asset)
                            if not file or not file.get('blob') or context.vault.files.ready(file): continue
                            for index in range(context.vault.files.count(file)):
                                if self.stopped.is_set(): return
                                if os.environ.get('WEIYAN_PLATFORM') == 'android':
                                    from java import jclass
                                    if not jclass('com.weiyan.poetry.MessageNotifications').isEnabled() and not jclass('com.weiyan.poetry.MainActivity').isForeground:
                                        raise OSError('后台接收已停止。')
                                path = context.vault.files.part_path(file, index)
                                if path.exists() and path.stat().st_size <= CHUNK_SIZE + OVERHEAD:
                                    try:
                                        context.vault.files.decode(file, index, path.read_bytes())
                                        continue
                                    except (ValueError, CryptoError):
                                        pass
                                raw = context.remote_binary('/api/blobs/' + file['blob']['transfer'] + '/' + str(index) + '?device_id=' + quote(context.device_id))
                                with context.vault.lock:
                                    if context.vault.get('file', asset) is None: break
                                    context.vault.files.write_cipher(file, index, raw)
                            if context.vault.get('file', asset) is not None:
                                context.vault.files.finish(asset)
                        context.remote('/api/received', {'device_id': context.device_id, 'id': int(mid)})
                        context.vault.delete('file_pending', mid)
                    except (OSError, ValueError, CryptoError, sqlite3.Error) as error:
                        if getattr(error, 'status', None) == 401:
                            self.stop()
                            return
                        failed = True
                for client_id, sealed in jobs:
                    job = context.vault.unpack('control_job', client_id, sealed)
                    try:
                        context.control_send(job['cid'], client_id, job['clear'], job['directory'], **job['extra'])
                        context.vault.delete('control_job', client_id)
                    except (OSError, ValueError, CryptoError, sqlite3.Error) as error:
                        if getattr(error, 'status', None) == 401:
                            self.stop()
                            return
                        failed = True
                if failed: self.wake.wait(10)
        finally:
            with self.lock:
                self.running = False
            if self.wake.is_set(): self.start()
