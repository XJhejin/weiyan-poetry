"""Exercise routing preservation, repeated deployment and failure rollback."""
import ast
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import deploy_domains as domains

ROOT = Path(__file__).resolve().parents[2]


def source_config():
    paths = ('官网/tools/deploy_site.py', '论坛/工具/deploy_forum.py', '个人主页/tools/deploy_site.py')
    blocks = []
    for path in paths:
        tree = ast.parse((ROOT / path).read_text('utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'site_block')
        blocks.append(ast.literal_eval(function.body[0].value))
    return (ROOT / '服务端/deploy/linux/Caddyfile.example').read_text('utf-8') + '\n' + '\n'.join(blocks)


class DomainDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.original = source_config()

    def test_real_handlers_and_original_routes_are_preserved(self):
        result = domains.build_candidate(self.original, domains.HOSTS)
        self.assertEqual(result.split(domains.BEGIN)[0].rstrip(), self.original.rstrip())
        self.assertEqual(domains.owned_hosts(result), set(domains.HOSTS))
        for label, source, aliases in domains.SITES:
            block = list(domains.managed_pattern('# BEGIN ' + label, '# END ' + label).finditer(self.original))[0][1].strip()
            body = block[len('https://' + source + ' {'):]
            self.assertIn(', '.join('https://' + host for host in aliases) + ' {' + body, result)
        self.assertEqual(result.count('max_size 1140850688'), 2)
        self.assertEqual(result.count('respond @private 404'), 2)
        self.assertIn('https://7me.top:23074, https://weiyan.ink:23074 {', result)
        self.assertEqual(result.count('profile shortlived'), 1)
        self.assertEqual(result.count('reverse_proxy 127.0.0.1:23174'), 2)

    def test_deployment_is_idempotent_and_does_not_treat_acme_as_a_site(self):
        first = domains.build_candidate(self.original, domains.HOSTS)
        self.assertEqual(domains.build_candidate(first, domains.HOSTS), first)
        self.assertNotIn('acme-v02.api.letsencrypt.org', domains.owned_hosts(first))

    def test_pending_root_can_be_added_without_replacing_subdomains(self):
        enabled = set(domains.HOSTS) - {'7me.top'}
        first = domains.build_candidate(self.original, enabled)
        self.assertEqual(domains.owned_hosts(first), enabled)
        second = domains.build_candidate(first, domains.HOSTS)
        self.assertEqual(domains.owned_hosts(second), set(domains.HOSTS))
        self.assertEqual(second, domains.build_candidate(self.original, domains.HOSTS))

    def test_refuses_unmanaged_host_and_unrecognized_sources(self):
        with self.assertRaises(ValueError):
            domains.build_candidate(self.original + '\nhttps://bbs.7me.top { respond 200 }\n', domains.HOSTS)
        with self.assertRaises(ValueError):
            domains.build_candidate(self.original.replace('# BEGIN Weiyan personal site', '# Unmanaged personal site'), domains.HOSTS)
        with self.assertRaises(ValueError):
            domains.build_candidate(self.original, {'unexpected.example'})

    def test_refuses_ambiguous_owned_sections(self):
        first = domains.build_candidate(self.original, domains.HOSTS)
        with self.assertRaises(ValueError):
            domains.build_candidate(first + '\n' + domains.BEGIN + '\n' + domains.END + '\n', domains.HOSTS)

    def test_http_redirect_preserves_requested_hostname_and_path(self):
        result = domains.nginx_candidate(domains.HOSTS)
        self.assertIn('return 301 https://$host$request_uri;', result)
        for host in domains.HOSTS:
            self.assertIn(host, result)

    def test_failed_activation_restores_both_proxy_configurations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config, nginx = root / 'Caddyfile', root / 'vhosts/aliases.conf'
            nginx.parent.mkdir()
            config.write_text(self.original, 'utf-8')
            nginx.write_text('# previous HTTP configuration\n', 'utf-8')
            previous = (config.read_bytes(), nginx.read_bytes())
            lock = types.SimpleNamespace(flock=lambda *args: None, LOCK_EX=1, LOCK_NB=2)
            def fail_verification(*args, **kwargs):
                self.assertEqual(domains.owned_hosts(config.read_text('utf-8')), set(domains.HOSTS))
                raise RuntimeError('Simulated HTTPS failure')
            with (patch.object(domains, 'BASE', root / 'state'), patch.object(domains, 'CONFIG', config),
                  patch.object(domains, 'NGINX_CONFIG', nginx), patch.object(domains.os, 'geteuid', return_value=0, create=True),
                  patch.dict(sys.modules, {'fcntl': lock}), patch.object(domains, 'dns_addresses', return_value=[domains.PUBLIC_IP]),
                  patch.object(domains, 'existing_health'), patch.object(domains, 'run') as commands,
                  patch.object(domains, 'verify_hosts', side_effect=fail_verification), contextlib.redirect_stdout(io.StringIO())):
                with self.assertRaisesRegex(RuntimeError, 'Simulated HTTPS failure'):
                    domains.deploy()
                self.assertEqual(config.read_bytes(), previous[0])
                self.assertEqual(nginx.read_bytes(), previous[1])
                restarts = [call for call in commands.call_args_list if call.args[0] == ['systemctl', 'restart', 'weiyan-proxy.service']]
                self.assertEqual(len(restarts), 2)
                backups = list((root / 'state/backups').glob('*/Caddyfile'))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), previous[0])


if __name__ == '__main__':
    unittest.main()
