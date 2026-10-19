"""Connection reuse and heartbeats must survive a slow encrypted inbox."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transport_http import HTTPPool
from network_status import NetworkStatus
import main


class TransportTests(unittest.TestCase):
    def test_probes_reuse_chat_connection_and_reject_oversized_responses(self):
        ports = []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_GET(self):
                ports.append(self.client_address[1])
                payload = b'x' * 8193 if self.path == '/large' else b'{"ok":true}'
                self.send_response(200);self.send_header('Content-Length', str(len(payload)))
                self.end_headers();self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler);server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        target = 'http://127.0.0.1:' + str(server.server_port)
        pool, status = HTTPPool(target), NetworkStatus()
        try:
            pool.request('GET', '/chat')
            with patch('network_status.pool_for', return_value=pool):
                self.assertIsNotNone(status.probe(target)['latency_ms'])
                status.last_started = 0
                self.assertEqual(status.probe(target)['samples'], 2)
            self.assertEqual(len(set(ports)), 1, 'Health checks opened new TCP connections')
            with self.assertRaises(ValueError): pool.request('GET', '/large', max_response_bytes=8192)
            self.assertTrue(pool.idle.empty(), 'Unconsumed response returned to the connection pool')
            self.assertEqual(pool.request('GET', '/chat')[0], 200)
            self.assertNotEqual(ports[-1], ports[-2])
        finally:
            while not pool.idle.empty(): pool.idle.get_nowait()[0].close()
            server.shutdown();server.server_close()

    def test_probe_failures_and_address_changes_are_not_hidden(self):
        pool, status = Mock(), NetworkStatus()
        with patch('network_status.pool_for', return_value=pool):
            for body in (b'{"ok":false}', b'[]', b'bad json'):
                pool.request.return_value = (200, [], body)
                status.last_started = 0
                self.assertIsNone(status.probe('https://first.invalid')['latency_ms'])
            pool.request.side_effect = OSError('offline')
            status.last_started = 0
            self.assertEqual(status.probe('https://first.invalid')['loss_rate'], 100)
            def change_target(*args, **kwargs):
                status.probe('https://second.invalid')
                return 200, [], b'{"ok":true}'
            pool.request.side_effect = change_target
            status.last_started = 0
            self.assertEqual(status.probe('https://first.invalid')['samples'], 0)
            pool.request.side_effect = None
            pool.request.return_value = (200, [], b'{"ok":true}')
            value = status.probe('https://second.invalid')
            self.assertEqual(value['samples'], 1)
            self.assertEqual(value['loss_rate'], 0)

    def test_reuse_and_headers_do_not_mix_accounts(self):
        connections, cookies = [], []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_GET(self):
                connections.append(self.client_address[1]);cookies.append(self.headers.get('Cookie'))
                self.send_response(200);self.send_header('Content-Length', '2');self.end_headers();self.wfile.write(b'{}')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler);server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        pool = HTTPPool('http://127.0.0.1:' + str(server.server_port))
        try:
            for cookie in ('alice', 'bob', 'alice'):
                self.assertEqual(pool.request('GET', '/', headers={'Cookie': cookie})[0], 200)
            self.assertEqual(len(set(connections)), 1)
            self.assertEqual(cookies, ['alice', 'bob', 'alice'])
        finally:
            while not pool.idle.empty(): pool.idle.get_nowait()[0].close()
            server.shutdown();server.server_close()

    def test_ambiguous_post_is_not_automatically_repeated(self):
        pool = HTTPPool('http://127.0.0.1:1')
        for method, count in (('POST', 1), ('GET', 2)):
            with patch('transport_http.http.client.HTTPConnection') as connection:
                connection.return_value.request.side_effect = OSError('connection lost')
                with self.assertRaises(OSError): pool.request(method, '/')
                self.assertEqual(connection.return_value.request.call_count, count)

    def test_slow_receiving_does_not_block_heartbeat_or_outgoing_ack(self):
        receive_started, release_receive, ping_sent = threading.Event(), threading.Event(), threading.Event()
        context = Mock(blocked=None, device_id='test-device', active_sockets=0)
        def receive():
            receive_started.set();release_receive.wait(8);return []
        context.receive.side_effect = receive
        context.send.return_value = {'id': 1, 'body': 'sent'}
        class Socket:
            def __init__(self): self.connected=True;self.inbox=queue.Queue();self.sent=[]
            def receive(self, timeout):
                try:return self.inbox.get(timeout=timeout)
                except queue.Empty:return None
            def send(self, value):
                self.sent.append(json.loads(value))
                if self is upstream and json.loads(value).get('op')=='ping':ping_sent.set()
            def close(self): self.connected=False
        upstream, local = Socket(), Socket()
        handler = object.__new__(main.Gateway);handler.headers={};handler.connection=Mock()
        handler.context = Mock(return_value=(context, {}));handler.relay_cookie=Mock(return_value='session')
        with patch('simple_websocket.Client.connect', return_value=upstream), patch('simple_websocket.Server', return_value=local):
            thread=threading.Thread(target=handler.websocket_proxy,daemon=True);thread.start()
            try:
                self.assertTrue(receive_started.wait(2))
                local.inbox.put(json.dumps({'op':'ping'}))
                local.inbox.put(json.dumps({'op':'message','client_id':'slow-receive-test','body':'sent'}))
                self.assertTrue(ping_sent.wait(2), 'Slow inbox prevented heartbeat forwarding')
                deadline=time.monotonic()+2
                while time.monotonic()<deadline and not any(v.get('type')=='ack' for v in local.sent):time.sleep(.01)
                self.assertTrue(any(v.get('type')=='ack' for v in local.sent))
            finally:
                local.close();upstream.close();release_receive.set();thread.join(3)
            self.assertFalse(thread.is_alive())


if __name__ == '__main__': unittest.main(verbosity=2)
