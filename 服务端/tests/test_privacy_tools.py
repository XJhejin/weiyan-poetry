"""Security boundaries: admin access, migration, signed update, rollback."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
from http.cookiejar import CookieJar
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '客户端'))
sys.path.insert(0, str(ROOT / '服务端'))
import server
import admin
import update
from secure import SecureClient, protect
from nacl.signing import SigningKey
from types import SimpleNamespace
from background import BackgroundAgent


class BackgroundTests(unittest.TestCase):
    def test_protected_session_restore_mute_and_logout(self):
        with tempfile.TemporaryDirectory(prefix='weiyan-background-') as folder:
            bootstrap={'me': {'id': 'background-user'}, 'server_id': 'background-test', 'offline': True}
            context=SecureClient(folder,'http://127.0.0.1:23074','weiyan_session=private-session-token',bootstrap)
            received=[]
            gateway=SimpleNamespace(contexts={})
            agent=BackgroundAgent(gateway,folder,lambda:context.url,received.append)
            agent.activate(context,bootstrap)
            self.assertNotIn(b'private-session-token',agent.saved.read_bytes())
            restored=BackgroundAgent(gateway,folder,lambda:context.url,received.append)
            restored.start()
            try:
                self.assertEqual(restored.active.device_id,context.device_id)
                self.assertEqual(restored.active.cookie,context.cookie)
                message={'sender':{'id':'peer'},'conversation_id':'dm:background'}
                restored.active.on_received(message)
                self.assertEqual(received,[message])
                restored.active.vault.put('ui','muted',['dm:background'])
                restored.active.on_received(message)
                restored.active.on_received({'sender':{'id':'background-user'},'conversation_id':'other'})
                self.assertEqual(received,[message])
                restored.clear()
                self.assertFalse(restored.saved.exists())
                self.assertIsNone(restored.active)
            finally:
                restored.stop()


class AdminTests(unittest.TestCase):
    def test_local_admin_token_origin_account_and_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix='weiyan-admin-') as folder:
            server.DATA=Path(folder);server.DB_PATH=server.DATA/'weiyan.sqlite3';server.initialize()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            controller=admin.Controller(server,'127.0.0.1',port);controller.start()
            http=admin.ThreadingHTTPServer(('127.0.0.1',0),admin.AdminHandler)
            http.daemon_threads=True;http.token='admin-test-token';http.controller=controller
            http.public=ROOT/'服务端'/'admin_ui';http.updater=update.Updater('server',folder)
            threading.Thread(target=http.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{http.server_port}'
            jar=CookieJar()
            opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            def request(path,data=None,expected=200,origin=None):
                headers={'X-Weiyan-Admin':'desktop','Content-Type':'application/json','Origin':origin or base}
                req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
                try:r=opener.open(req,timeout=10)
                except urllib.error.HTTPError as error:r=error
                with r:
                    raw=r.read();self.assertEqual(r.status,expected,raw)
                    return json.loads(raw) if r.headers.get_content_type()=='application/json' else raw
            try:
                request('/manage/state',expected=403)
                self.assertIn('服务端管理'.encode(),request('/?token=admin-test-token'))
                self.assertTrue(request('/manage/state')['running'])
                request('/manage/action',{'action':'stop'},expected=403,origin='https://untrusted.example')
                request('/manage/action',{'action':'settings','registration':False})
                self.assertEqual(request('/manage/state')['settings']['registration'],'0')
                self.assertIsNone(request('/manage/state')['privacy']['retention_days'])
                self.assertIsNone(request('/manage/state')['privacy']['queue_limit_mb'])
                self.assertFalse(request('/manage/state')['privacy']['history']!='client_only')
                with server.connect() as db:
                    db.execute("INSERT INTO users(id,username,nickname,password,avatar,color,bio,is_guest,created_at) VALUES ('test-member','member','掇花者','!','bunny','#ffe4ec','',0,?)",(server.now(),))
                request('/manage/action',{'action':'disable','user_id':'test-member'})
                self.assertTrue(request('/manage/state')['accounts'][0]['disabled'])
                request('/manage/action',{'action':'enable','user_id':'test-member'})
                self.assertFalse(request('/manage/state')['accounts'][0]['disabled'])
                request('/manage/action',{'action':'stop'})
                self.assertFalse(request('/manage/state')['running'])
                request('/manage/action',{'action':'start','host':'127.0.0.1','port':port})
                self.assertTrue(request('/manage/state')['running'])
                # Management never appears on the LAN relay endpoint.
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/manage/state')
                self.assertEqual(error.exception.code,404)
            finally:
                controller.stop();http.shutdown();http.server_close()


class MigrationTests(unittest.TestCase):
    def test_legacy_history_is_moved_and_database_scrubbed(self):
        with tempfile.TemporaryDirectory(prefix='weiyan-migration-') as folder:
            data=Path(folder)/'server';data.mkdir();profile=Path(folder)/'client'
            db=sqlite3.connect(data/'old.sqlite3');db.row_factory=sqlite3.Row
            db.executescript('''
            CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT,nickname TEXT,password TEXT,avatar TEXT,color TEXT,bio TEXT,is_guest INTEGER,created_at TEXT);
            CREATE TABLE members(conversation_id TEXT,user_id TEXT,last_read_id INTEGER);
            CREATE TABLE uploads(id TEXT,owner_id TEXT,filename TEXT,mime TEXT,created_at TEXT);
            CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation_id TEXT,sender_id TEXT,body TEXT,image_id TEXT,created_at TEXT,client_id TEXT);
            INSERT INTO users VALUES ('alice','alice','甲','!','bunny','#ffe4ec','',0,'2026-10-18T00:00:00Z');
            INSERT INTO users VALUES ('bob','bob','乙','!','bunny','#ffe4ec','',0,'2026-10-18T00:00:00Z');
            INSERT INTO members VALUES ('dm:alice:bob','alice',0);
            INSERT INTO members VALUES ('dm:alice:bob','bob',0);
            INSERT INTO messages VALUES (9,'dm:alice:bob','alice','legacy-secret-marker',NULL,'2026-10-18T00:00:00Z','legacy-1');
            ''');db.commit()
            with patch.dict(os.environ,{'WEIYAN_MIGRATION_DIR':str(profile/'legacy')}):
                server.relay.initialize(db,data);db.commit()
            sid=server.relay.setting(db,'server_id');db.close()
            self.assertNotIn(b'legacy-secret-marker',(data/'old.sqlite3').read_bytes())
            archives=list((profile/'legacy').glob('*.dpapi'));self.assertEqual(len(archives),1)
            self.assertNotIn(b'legacy-secret-marker',archives[0].read_bytes())
            bootstrap={'me':{'id':'bob'},'server_id':sid,'offline':True}
            client=SecureClient(profile,'http://127.0.0.1:1','',bootstrap)
            self.assertEqual(client.vault.messages('dm:alice:bob')[0]['body'],'legacy-secret-marker')
            stranger=SecureClient(profile,'http://127.0.0.1:1','',{'me':{'id':'eve'},'server_id':sid,'offline':True})
            self.assertEqual(stranger.vault.messages(),[])


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.key=SigningKey.generate();self.pub=base64.b64encode(bytes(self.key.verify_key)).decode()

    def signed(self,archive):
        data={'format':1,'repository':update.REPOSITORY,'version':'2.0.1','assets':{'client':{'name':'client.zip','sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size}}}
        data['signature']=base64.b64encode(self.key.sign(update.MANIFEST_DOMAIN+update.canonical(data)).signature).decode();return data

    def test_signature_tamper_and_unsafe_zip_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'bad.zip'
            with zipfile.ZipFile(file,'w') as z:z.writestr('客户端/../../data/evil','bad')
            with self.assertRaises(ValueError):update.validate_archive(file,'client')
            manifest=self.signed(file)
            self.assertEqual(update.verify_manifest(manifest,self.pub)['version'],'2.0.1')
            manifest['assets']['client']['sha256']='0'*64
            with self.assertRaises(ValueError):update.verify_manifest(manifest,self.pub)

    def test_install_preserves_data_and_rolls_back_on_failure(self):
        for fail in (False,True):
            with self.subTest(rollback=fail),tempfile.TemporaryDirectory() as folder:
                base=Path(folder);target=base/'app';target.mkdir();(target/'data').mkdir();(target/'data'/'keep.txt').write_text('keep')
                (target/'未言诗集.exe').write_bytes(b'old-exe');(target/'_internal').mkdir();(target/'_internal'/'runtime.txt').write_text('old-runtime')
                stage=base/'stage';stage.mkdir();archive=stage/'package.zip'
                with zipfile.ZipFile(archive,'w') as z:z.writestr('客户端/未言诗集.exe',b'new-exe');z.writestr('客户端/_internal/runtime.txt','new-runtime')
                manifest=self.signed(archive);job=stage/'job.json';job.write_text(json.dumps({'kind':'client','manifest':manifest,'target':str(target),'parent_pid':0,'archive':str(archive)}),encoding='utf-8')
                original_verify=update.verify_manifest;original_move=update.shutil.move
                def move(src,dst):
                    if fail and src.endswith('unpacked\\客户端\\_internal'):raise OSError('simulated install failure')
                    return original_move(src,dst)
                with patch.object(update,'verify_manifest',lambda d:original_verify(d,self.pub)),patch.object(update.subprocess,'Popen'),patch.object(update.shutil,'move',side_effect=move):
                    if fail:
                        with self.assertRaises(OSError):update.apply_update(job)
                    else:update.apply_update(job)
                self.assertEqual((target/'data'/'keep.txt').read_text(),'keep')
                self.assertEqual((target/'未言诗集.exe').read_bytes(),b'old-exe' if fail else b'new-exe')
                self.assertEqual((target/'_internal'/'runtime.txt').read_text(),'old-runtime' if fail else 'new-runtime')


if __name__=='__main__':unittest.main(verbosity=2)
