"""Integration tests use temporary data and never touch real conversations."""
import base64
import sqlite3
import hashlib
import copy
import concurrent.futures
import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from simple_websocket import Client


class PortAwareClient(Client):
    def handshake(self):
        hostname = self.host
        self.host = f"{hostname}:{self.port}"
        try:
            super().handshake()
        finally:
            self.host = hostname

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Session:
    def __init__(self, base):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, path, data=None, expected=200):
        headers = {"Content-Type": "application/json", "X-Weiyan-Client": "web", "Origin": self.base}
        raw = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=raw, headers=headers)
        try:
            response = self.opener.open(req, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        body = response.read()
        assert response.status == expected, (path, response.status, body.decode(errors="replace"))
        if response.headers.get_content_type() == "application/json":
            return json.loads(body)
        return body

    def websocket(self):
        cookie = "; ".join(f"{c.name}={c.value}" for c in self.cookies)
        return PortAwareClient.connect(self.base.replace("http", "ws", 1) + "/api/socket", headers={"Cookie": cookie, "Origin": self.base})


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


class ChatIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="weiyan-e2ee-")
        cls.env = os.environ.copy()
        cls.env["WEIYAN_DATA_DIR"] = str(Path(cls.temp.name) / "server")
        cls.env["WEIYAN_CLIENT_DATA"] = str(Path(cls.temp.name) / "client")
        cls.server_port, cls.client_port, cls.second_port = free_port(), free_port(), free_port()
        cls.server_base = f"http://127.0.0.1:{cls.server_port}"
        cls.client_base = f"http://127.0.0.1:{cls.client_port}"
        cls.second_base = f"http://127.0.0.1:{cls.second_port}"
        cls.server = cls.start_server()
        cls.gateway = cls.start_client(cls.client_port, cls.env)
        cls.other_gateway = None
        cls.alice, cls.bob, cls.eve = (Session(cls.client_base) for _ in range(3))
        cls.alice_id = cls.alice.request('/api/register',dict(username='alice_test',password='test-password-a',nickname='甲'))['me']['id']
        cls.bob_id = cls.bob.request('/api/register',dict(username='bob_test',password='test-password-b',nickname='乙'))['me']['id']
        cls.eve_id = cls.eve.request('/api/register',dict(username='eve_test',password='test-password-c',nickname='旁观者'))['me']['id']
        for session in (cls.alice,cls.bob,cls.eve):session.request('/api/bootstrap')
        cls.cid=cls.alice.request('/api/conversations',{'peer_id':cls.bob_id})['conversation']['id']

    @classmethod
    def start_server(cls):
        entry=[str(ROOT/'服务端'/'未言诗集服务端.exe')] if os.environ.get('WEIYAN_TEST_BINARIES') else [sys.executable,str(ROOT/'服务端'/'server.py')]
        process=subprocess.Popen(entry+['--headless','--host','127.0.0.1','--port',str(cls.server_port)],env=cls.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.wait_for(cls.server_base+'/api/health');return process

    @classmethod
    def start_client(cls,port,env):
        entry=[str(ROOT/'客户端'/'未言诗集.exe')] if os.environ.get('WEIYAN_TEST_BINARIES') else [sys.executable,str(ROOT/'客户端'/'main.py')]
        process=subprocess.Popen(entry+['--browser','--no-open','--port',str(port),'--server',cls.server_base],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.wait_for(f'http://127.0.0.1:{port}/desktop/config');return process

    @staticmethod
    def wait_for(url):
        for _ in range(150):
            try:
                with urllib.request.urlopen(url,timeout=1):return
            except (OSError,urllib.error.URLError):time.sleep(.1)
        raise AssertionError('Process did not become ready: '+url)

    @classmethod
    def tearDownClass(cls):
        for process in (cls.other_gateway,cls.gateway,cls.server):
            if process:cls.stop_process(process)
        cls.temp.cleanup()

    @staticmethod
    def stop_process(process):
        if os.name=='nt' and os.environ.get('WEIYAN_TEST_BINARIES'):
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        else:process.terminate()
        process.wait(timeout=15)

    def db(self):
        db=sqlite3.connect(Path(self.env['WEIYAN_DATA_DIR'])/'weiyan.sqlite3',factory=ClosingConnection);db.row_factory=sqlite3.Row;return db

    def send(self,body,mid,cid=None,image_id=None):
        return self.alice.request('/api/messages',dict(conversation_id=cid or self.cid,body=body,client_id=mid,image_id=image_id),expected=201)['message']

    def assert_queue_cleared(self,mid):
        # The response can arrive while the final SQLite context is committing.
        # Wait for that transaction without initiating additional delivery work.
        pending=[]
        for _ in range(100):
            with self.db() as db:
                exists=db.execute('select 1 from relay_packets where id=?',(mid,)).fetchone()
                pending=[tuple(r) for r in db.execute('select device_id from deliveries where packet_id=?',(mid,))]
            if exists is None:return
            time.sleep(.05)
        self.fail(f'Ciphertext was not deleted after confirmations; pending devices: {pending}')

    def raw_server(self,session):
        remote=Session(self.server_base);remote.cookies=session.cookies;remote.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(remote.cookies));return remote

    def test_01_auth_separation_and_key_custody(self):
        self.assertIn(b'app.js',Session(self.client_base).request('/'))
        self.assertEqual(Session(self.server_base).request('/')['app'],'未言诗集服务端')
        Session(self.client_base).request('/api/bootstrap',expected=401)
        Session(self.client_base).request('/api/login',dict(username='alice_test',password='wrong'),expected=401)
        info=self.alice.request('/desktop/security?conversation_id='+self.cid)
        self.assertTrue(info['encrypted']);self.assertTrue(info['local_encrypted'])
        self.assertEqual(len(info['devices']),2)
        remote=self.raw_server(self.eve)
        remote.request('/api/keys?conversation_id='+self.cid,expected=403)
        with self.db() as db:
            columns={r[1] for r in db.execute('pragma table_info(devices)')}
            self.assertNotIn('private_key',columns)
            self.assertEqual(db.execute('select count(*) from devices').fetchone()[0],3)

    def test_02_private_text_image_offline_and_no_plaintext_on_server(self):
        marker='privacy-marker-6a4f-测试私语'
        png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
        image=self.alice.request('/api/upload',{'data':png})
        self.eve.request(image['url'],expected=404)
        message=self.send(marker,'private-001',image_id=image['id'])
        with self.db() as db:
            tables={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
            self.assertNotIn('messages',tables);self.assertNotIn('uploads',tables)
            packet=db.execute('select envelope from relay_packets where id=?',(message['id'],)).fetchone()[0]
            self.assertNotIn(marker.encode(),packet)
            envelope=json.loads(packet);self.assertNotIn('body',envelope);self.assertNotIn(self.eve_id,envelope['keys'])
        for file in Path(self.env['WEIYAN_DATA_DIR']).glob('*'):
            if file.is_file():self.assertNotIn(marker.encode(),file.read_bytes())
        self.eve.request('/api/conversations/'+self.cid+'/messages',expected=403)
        history=self.bob.request('/api/conversations/'+self.cid+'/messages')['messages']
        received=next(m for m in history if m['client_id']=='private-001')
        self.assertEqual(received['body'],marker);self.assertTrue(received['encrypted'])
        self.assertEqual(self.bob.request(received['image']),base64.b64decode(png))
        self.assertEqual(self.eve.request('/api/search?q='+urllib.parse.quote(marker))['messages'],[])
        self.assert_queue_cleared(message['id'])
        self.assertFalse((Path(self.env['WEIYAN_DATA_DIR'])/'uploads').exists())
        for file in Path(self.env['WEIYAN_CLIENT_DATA']).rglob('history.sqlite3*'):
            self.assertNotIn(marker.encode(),file.read_bytes())
        remote=self.raw_server(self.alice)
        remote.request('/api/messages',dict(body='plaintext'),expected=410)
        remote.request('/api/conversations/'+self.cid+'/messages',expected=410)
        remote.request('/api/search?q=test',expected=410)
        remote.request('/api/upload',dict(data=png),expected=410)

    def test_03_websocket_ack_retransmission_tamper_and_heartbeat(self):
        a,b=self.alice.websocket(),self.bob.websocket()
        def event(ws,wanted,client_id=None):
            until=time.time()+15
            while time.time()<until:
                raw=ws.receive(timeout=2)
                if raw is None:continue
                value=json.loads(raw)
                if value.get('type')==wanted and (client_id is None or value.get('client_id',value.get('message',{}).get('client_id'))==client_id):return value
            self.fail('Missing websocket event '+wanted)
        try:
            payload=dict(op='message',conversation_id=self.cid,body='WebSocket encrypted delivery',client_id='websocket-001')
            a.send(json.dumps(payload));ack=event(a,'ack','websocket-001');self.assertTrue(ack['message']['encrypted'])
            self.assertEqual(event(b,'message','websocket-001')['message']['body'],payload['body'])
            a.send(json.dumps(payload));self.assertEqual(event(a,'ack','websocket-001')['message']['id'],ack['message']['id'])
            a.send('[]');event(a,'nack');a.send(json.dumps({'op':'ping'}));event(a,'pong')
        finally:a.close();b.close()
        m=self.send('Tamper protected','tamper-001')
        with self.db() as db:packet=json.loads(db.execute('select envelope from relay_packets where id=?',(m['id'],)).fetchone()[0])
        original=copy.deepcopy(packet);packet['ciphertext']=base64.b64encode(b'x'*80).decode()
        remote=self.raw_server(self.alice);remote.request('/api/relay',{'envelope':packet},expected=400)
        duplicate=remote.request('/api/relay',{'envelope':original},expected=201);self.assertEqual(duplicate['id'],m['id'])
        other=self.raw_server(self.eve);other.request('/api/relay',{'envelope':original},expected=403)
        self.bob.request('/api/bootstrap')

    def test_04_encrypted_drafts_bookmarks_and_csrf(self):
        value='secret-draft-41f8-不可明文保存'
        self.alice.request('/desktop/storage',{'values':{'drafts':{self.cid:value},'saved':[{'body':value}]}})
        self.assertEqual(self.alice.request('/api/bootstrap')['local_storage']['drafts'][self.cid],value)
        for file in Path(self.env['WEIYAN_CLIENT_DATA']).rglob('*'):
            if file.is_file() and file.suffix not in ('.log',):self.assertNotIn(value.encode(),file.read_bytes())
        req=urllib.request.Request(self.client_base+'/desktop/storage',data=b'{}',headers={'Content-Type':'application/json','X-Weiyan-Client':'web','Origin':'https://untrusted.example'})
        with self.assertRaises(urllib.error.HTTPError) as error:self.alice.opener.open(req)
        self.assertEqual(error.exception.code,403)
        self.alice.request('/api/messages',dict(conversation_id=self.cid,body='x'*2001,client_id='invalid-body'),expected=400)
        self.alice.request('/api/upload',dict(data=base64.b64encode(b'<script>bad</script>').decode()),expected=400)

    def test_05_new_device_requires_fingerprint_confirmation(self):
        second_env=self.env.copy();second_env['WEIYAN_CLIENT_DATA']=str(Path(self.temp.name)/'second-client')
        type(self).other_gateway=self.start_client(self.second_port,second_env)
        bob_new=Session(self.second_base);bob_new.request('/api/login',dict(username='bob_test',password='test-password-b'));bob_new.request('/api/bootstrap')
        self.alice.request('/api/messages',dict(conversation_id=self.cid,body='verify new device',client_id='new-device-001'),expected=400)
        info=self.alice.request('/desktop/security?conversation_id='+self.cid);self.assertTrue(info['key_changed'])
        self.alice.request('/desktop/security',dict(conversation_id=self.cid,approve=True,directory_digest='wrong'),expected=400)
        self.alice.request('/desktop/security',dict(conversation_id=self.cid,approve=True,directory_digest=info['directory_digest']))
        self.send('Verified device delivery','new-device-001')
        new_history=bob_new.request('/api/conversations/'+self.cid+'/messages')['messages']
        self.assertEqual([m['client_id'] for m in new_history],['new-device-001'])
        self.bob.request('/api/bootstrap')
        # Later room sends also require recognizing this new device for the sender.
        self.alice.request('/desktop/security?conversation_id=room:inspiration')
        type(self).new_bob=bob_new

    def test_06_local_pagination_server_and_client_restart_offline_history(self):
        for index in range(53):self.send('Pagination '+str(index),'page-'+str(index))
        first=self.bob.request('/api/conversations/'+self.cid+'/messages')
        self.assertEqual(len(first['messages']),50);self.assertTrue(first['has_more'])
        second=self.bob.request('/api/conversations/'+self.cid+'/messages?before='+str(first['messages'][0]['id']))
        self.assertFalse({m['id'] for m in first['messages']}&{m['id'] for m in second['messages']})
        self.new_bob.request('/api/bootstrap')
        # Closing the relay cannot remove already received local history or drafts.
        self.stop_process(self.server)
        cached=self.bob.request('/api/bootstrap');self.assertTrue(cached['offline'])
        self.bob.request('/desktop/storage',{'values':{'drafts':{self.cid:'offline encrypted draft'}}})
        self.stop_process(self.gateway);type(self).gateway=self.start_client(self.client_port,self.env)
        self.assertTrue(self.bob.request('/api/bootstrap')['offline'])
        self.assertEqual(self.bob.request('/api/conversations/'+self.cid+'/messages')['messages'][-1]['body'],'Pagination 52')
        type(self).server=self.start_server()
        self.assertEqual(self.bob.request('/api/bootstrap')['me']['id'],self.bob_id)
        self.assertEqual(self.bob.request('/api/bootstrap')['local_storage']['drafts'][self.cid],'offline encrypted draft')

    def test_07_pending_ciphertext_never_expires_and_ack_is_scoped(self):
        # Deprecated limits must not cap the queue or expire unreceived data.
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('queue_limit_mb','0')")
            db.execute("INSERT OR REPLACE INTO settings VALUES ('retention_days','1')")
        msg=self.send('Keep until received','permanent-001')
        info=self.alice.request('/desktop/security?conversation_id='+self.cid)
        bob_device=next(d['device_id'] for d in info['devices'] if d['user_id']==self.bob_id)
        self.raw_server(self.eve).request('/api/received',dict(device_id=bob_device,id=msg['id']),expected=403)
        with self.db() as db:
            db.execute('update receipts set created_at=? where id=?',(time.time()-10*365*86400,msg['id']))
        # Restart cleanup must retain the packet, even if it is ten years old.
        self.stop_process(self.server);type(self).server=self.start_server()
        with self.db() as db:
            self.assertIsNotNone(db.execute('select 1 from relay_packets where id=?',(msg['id'],)).fetchone())
        info=self.bob.request('/desktop/security?conversation_id='+self.cid)
        self.assertIsNone(info['privacy']['retention_days']);self.assertIsNone(info['privacy']['queue_limit_mb'])
        history=self.bob.request('/api/conversations/'+self.cid+'/messages')['messages']
        self.assertTrue(any(m['client_id']=='permanent-001' for m in history))
        self.new_bob.request('/api/bootstrap')
        self.assert_queue_cleared(msg['id'])

    def test_08_five_megabyte_image_roundtrip(self):
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        raw=png+b'\0'*(5*1024*1024-len(png))
        image=self.alice.request('/api/upload',{'data':base64.b64encode(raw).decode()})
        self.send('5 MB encrypted image','image-boundary',image_id=image['id'])
        history=self.bob.request('/api/conversations/'+self.cid+'/messages')['messages']
        message=next(m for m in history if m['client_id']=='image-boundary')
        self.assertEqual(self.bob.request(message['image']),raw)


if __name__=='__main__':unittest.main(verbosity=2)
