"""Real socket checks for configurable relay bindings and persisted settings."""
import http.client
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '服务端'))
import admin
import server
from listening import listen_address, listen_port, connection_addresses


def available_port(host):
    with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


class ListeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='weiyan-listen-')
        data = Path(self.temp.name)
        self.paths = patch.multiple(server, DATA=data, DB_PATH=data / 'weiyan.sqlite3')
        self.paths.start()
        server.initialize()
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.stop()
        self.paths.stop()
        self.temp.cleanup()

    def controller(self, host, port):
        controller = admin.Controller(server, host, port)
        self.controllers.append(controller)
        return controller

    def health(self, host, port):
        connection = http.client.HTTPConnection(host, port, timeout=3)
        try:
            connection.request('GET', '/api/health')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read())['ok'])
            connection.request('GET', '/manage/state')
            response = connection.getresponse()
            self.assertEqual(response.status, 404)
            response.read()
        finally:
            connection.close()

    def test_custom_ipv4_really_binds_and_survives_restart(self):
        try:
            port = available_port('127.0.0.2')
        except OSError:
            self.skipTest('OS does not provide the secondary loopback address')
        controller = self.controller('0.0.0.0', port)
        controller.start(' 127.0.0.2 ', str(port))
        self.assertEqual(controller.server.socket.getsockname()[:2], ('127.0.0.2', port))
        self.assertEqual(controller.state()['addresses'], [f'http://127.0.0.2:{port}'])
        self.health('127.0.0.2', port)
        controller.stop()
        with server.connect() as db:
            host = server.relay.setting(db, 'listen_host')
            saved_port = int(server.relay.setting(db, 'listen_port'))
        self.assertEqual((host, saved_port), ('127.0.0.2', port))
        restored = self.controller(host, saved_port)
        restored.start()
        self.health(host, saved_port)

    def test_invalid_values_never_replace_saved_configuration(self):
        port = available_port('127.0.0.1')
        controller = self.controller('127.0.0.1', port)
        controller.start()
        controller.stop()
        for host, candidate_port in [('http://127.0.0.1', port), ('127.0.0.1:23074', port),
                                     ('192.168.1.0/24', port), ('', port), ('127.0.0.1', 0),
                                     ('127.0.0.1', 65536), ('127.0.0.1', 1.5), ('127.0.0.1', True)]:
            with self.subTest(host=host, port=candidate_port), self.assertRaises(ValueError):
                controller.start(host, candidate_port)
            self.assertIsNone(controller.server)
            self.assertTrue(controller.error)
            with server.connect() as db:
                self.assertEqual(server.relay.setting(db, 'listen_host'), '127.0.0.1')
                self.assertEqual(int(server.relay.setting(db, 'listen_port')), port)

    def test_port_conflict_can_be_corrected_without_losing_data(self):
        (server.DATA / 'preserve.txt').write_text('keep')
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            controller = self.controller('127.0.0.1', port)
            with self.assertRaisesRegex(ValueError, '端口'):
                controller.start()
            self.assertIsNone(controller.server)
        controller.start()
        self.health('127.0.0.1', port)
        self.assertEqual(controller.error, '')
        self.assertEqual((server.DATA / 'preserve.txt').read_text(), 'keep')

    def test_ipv6_listener_and_connection_urls(self):
        try:
            port = available_port('::1')
        except OSError:
            self.skipTest('IPv6 loopback unavailable')
        controller = self.controller('[::1]', port)
        controller.start()
        self.assertEqual(controller.state()['addresses'], [f'http://[::1]:{port}'])
        self.assertEqual(controller.server.socket.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY), 1)
        self.health('::1', port)
        controller.stop()
        controller.start('::', port)
        self.health('::1', port)
        self.assertIn(f'http://[::1]:{port}', controller.state()['addresses'])
        self.assertNotIn(f'http://127.0.0.1:{port}', controller.state()['addresses'])

    def test_wildcard_addresses_match_bound_family(self):
        def results(hostname, port, family, kind):
            return [(family, kind, 6, '', ('192.168.1.10', 0)),
                    (family, kind, 6, '', ('127.0.0.1', 0))]
        with patch('listening.socket.getaddrinfo', side_effect=results):
            self.assertEqual(connection_addresses('0.0.0.0', 23074),
                             ['http://127.0.0.1:23074', 'http://192.168.1.10:23074'])
        self.assertEqual(connection_addresses('192.168.1.10', 23074), ['http://192.168.1.10:23074'])
        self.assertEqual(listen_address(' [2001:db8::1] '), '2001:db8::1')
        self.assertEqual(listen_port(' 23074 '), 23074)


if __name__ == '__main__':
    unittest.main(verbosity=2)
