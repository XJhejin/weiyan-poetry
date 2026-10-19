"""Attachment bounds, authentication and bounded reads, without allocating 10 GiB."""
import base64
import copy
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import secure
from large_files import CHUNK_SIZE, MAX_SIZE, checked_file


class AttachmentStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Isolated test wrapping also works on Linux; never enters the application.
        box = SecretBox(secrets.token_bytes(32))
        self.wrapping = patch.object(secure, 'protect', side_effect=lambda raw, decrypt=False: box.decrypt(raw) if decrypt else bytes(box.encrypt(raw)))
        self.wrapping.start()
        self.vault = secure.Vault(Path(self.temp.name))
        self.files = self.vault.files

    def tearDown(self):
        self.wrapping.stop()
        self.temp.cleanup()

    def test_limit_for_every_kind_without_whole_file_allocation(self):
        for kind in ('image', 'video', 'file'):
            value = {'name': 'boundary.bin', 'size': MAX_SIZE, 'kind': kind}
            self.assertEqual(checked_file(value)['size'], MAX_SIZE)
            asset = self.files.begin(value)
            self.assertEqual(asset['parts'], 10240)
            self.assertEqual(sum(p.stat().st_size for p in self.files.root.rglob('*') if p.is_file()), 0)
            for invalid in (MAX_SIZE+1, -1, True, '10737418240'):
                with self.assertRaises(ValueError): checked_file({**value, 'size': invalid})

    def test_ciphertext_authentication_binds_file_and_part(self):
        raw = secrets.token_bytes(CHUNK_SIZE)
        asset = self.files.begin({'name': 'parts.bin', 'size': CHUNK_SIZE*2, 'kind': 'file'})['id']
        for index in (0, 1): self.files.put(asset, index, raw)
        self.files.finish(asset)
        file = self.vault.get('file', asset)
        cipher = self.files.part_path(file, 0).read_bytes()
        self.assertNotIn(raw[:128], cipher)
        with self.assertRaises(ValueError): self.files.decode(file, 1, cipher)
        with self.assertRaises(ValueError): self.files.decode(file, 0, cipher[:-1])
        with self.assertRaises(CryptoError): self.files.decode(file, 0, cipher[:-1]+bytes([cipher[-1]^1]))
        other = copy.deepcopy(file);other['blob']['id'] = 'f'*32
        with self.assertRaises(ValueError): self.files.decode(other, 0, cipher)
        other = copy.deepcopy(file);other['blob']['key'] = base64.b64encode(secrets.token_bytes(32)).decode()
        with self.assertRaises(CryptoError): self.files.decode(other, 0, cipher)

    def test_range_iterator_reads_at_most_one_encrypted_part(self):
        asset = self.files.begin({'name': 'range.bin', 'size': CHUNK_SIZE*2+17, 'kind': 'file'})['id']
        for index in range(3): self.files.put(asset, index, bytes([index])*min(CHUNK_SIZE, CHUNK_SIZE*2+17-index*CHUNK_SIZE))
        self.files.finish(asset)
        file = self.vault.get('file', asset)
        reads = []
        original = Path.read_bytes
        def bounded(path):
            value = original(path);reads.append(len(value));return value
        with patch.object(Path, 'read_bytes', bounded):
            result = b''.join(self.files.read_range(file, CHUNK_SIZE-5, CHUNK_SIZE+8))
        self.assertEqual(result, b'\0'*5+b'\1'*9)
        self.assertEqual(len(reads), 2)
        self.assertLessEqual(max(reads), CHUNK_SIZE+88)
        self.assertEqual(self.files.status(asset)['received'], file['size'])

    def test_partial_reopen_rejects_changed_content_and_completes(self):
        asset = self.files.begin({'name': 'resume.bin', 'size': CHUNK_SIZE+2, 'kind': 'file'})['id']
        self.files.put(asset, 0, b'a'*CHUNK_SIZE)
        with self.assertRaises(ValueError): self.files.finish(asset)
        reopened = secure.Vault(Path(self.temp.name))
        with self.assertRaises(ValueError): reopened.files.put(asset, 0, b'b'*CHUNK_SIZE)
        reopened.files.put(asset, 1, b'bc')
        reopened.files.finish(asset)
        self.assertTrue(reopened.files.status(asset)['ready'])

    def test_signed_envelope_references_must_match_encrypted_descriptor(self):
        asset = self.files.begin({'name': 'signed.bin', 'size': 1, 'kind': 'file'})['id']
        file = self.vault.get('file', asset);file['blob']['transfer'] = 'e'*32
        clear = {'attachment': file}
        secure.SecureClient.check_blob_references({'blobs': ['e'*32]}, clear)
        for declared in ([], ['a'*32], ['e'*32]*2, [{}]):
            with self.assertRaises(ValueError): secure.SecureClient.check_blob_references({'blobs': declared}, clear)


if __name__ == '__main__': unittest.main(verbosity=2)
