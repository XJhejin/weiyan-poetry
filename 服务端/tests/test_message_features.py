"""Quotes, explicit mentions, recall privacy and immediate encrypted local history."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '客户端'))
from test_chat import ChatHarness
from message_features import checked_mentions, checked_quote, quote_from, mentioned
from background import BackgroundAgent
from secure import SecureClient
from network_status import NetworkStatus


class MessageFeatureIntegration(ChatHarness):
    def test_01_quotes_are_structured_encrypted_and_removed_after_recall(self):
        original = self.send('quote-private-marker-001', 'quote-original')
        self.history_until(self.bob, lambda rows: any(m['id'] == original['id'] for m in rows))
        reference = quote_from(original)
        # A sender-provided forged snapshot is replaced by the real local source.
        forged = {**reference, 'excerpt': 'forged source', 'sender_name': 'forged author'}
        reply = self.bob.request('/api/messages', {'conversation_id': self.cid, 'client_id': 'quote-reply', 'body': '我的回复', 'quote': forged}, expected=201)['message']
        self.assertEqual(reply['body'], '我的回复')
        self.assertEqual(reply['quote'], reference)
        with self.db() as db:
            raw = db.execute('SELECT envelope FROM relay_packets WHERE id=?', (reply['id'],)).fetchone()[0]
            self.assertNotIn(b'quote-private-marker', raw)
            self.assertNotIn(b'"quote"', raw)
        got = self.history_until(self.alice, lambda rows: any(m['id'] == reply['id'] for m in rows))['messages']
        self.assertEqual(next(m for m in got if m['id'] == reply['id'])['quote'], reference)
        self.bob.request('/desktop/storage', {'values': {'drafts': {self.cid: {'body': 'draft', 'quote': reference}}, 'saved': [reply]}})
        self.alice.request('/api/recall', {'id': original['id'], 'client_id': original['client_id']})
        for session in (self.alice, self.bob):
            self.history_until(session, lambda rows: any(m['id'] == original['id'] and m.get('recalled') for m in rows))
            rows = session.request('/api/conversations/' + self.cid + '/messages?local=1')['messages']
            q = next(m for m in rows if m['id'] == reply['id'])['quote']
            self.assertTrue(q['recalled'])
            self.assertEqual(q['excerpt'], '消息已撤回')
        prefs = self.bob.request('/api/bootstrap')['local_storage']
        self.assertEqual(prefs['drafts'][self.cid]['quote']['excerpt'], '消息已撤回')
        self.assertTrue(prefs['saved'][0]['quote']['recalled'])

    def test_02_quote_from_another_conversation_is_rejected(self):
        original = self.send('quote scope', 'quote-scope')
        payload = {'conversation_id': 'room:lobby', 'body': 'wrong scope', 'client_id': 'quote-wrong', 'quote': quote_from(original)}
        self.alice.request('/api/messages', payload, expected=400)
        self.alice.request('/api/messages', {**payload, 'conversation_id': self.cid, 'quote': {**quote_from(original), 'id': original['id'] + 99999}}, expected=400)

    def test_03_mention_targets_are_real_members_and_ranges_survive_unicode(self):
        body = '🌷 @乙 请查看'
        mentions = [{'user_id': self.bob_id, 'start': 2, 'length': 2}]
        message = self.alice.request('/api/messages', {'conversation_id': self.cid, 'client_id': 'mention-unicode', 'body': body, 'mentions': mentions}, expected=201)['message']
        received = self.history_until(self.bob, lambda rows: any(m['id'] == message['id'] for m in rows))['messages']
        self.assertEqual(next(m for m in received if m['id'] == message['id'])['mentions'], mentions)
        self.assertTrue(mentioned(message, self.bob_id))
        self.assertFalse(mentioned(message, self.eve_id))
        self.alice.request('/api/messages', {'conversation_id': self.cid, 'client_id': 'mention-outsider', 'body': body, 'mentions': [{**mentions[0], 'user_id': self.eve_id}]}, expected=400)
        users = self.alice.request('/api/participants?conversation_id=' + self.cid)['users']
        self.assertEqual({u['id'] for u in users}, {self.alice_id, self.bob_id})
        self.eve.request('/api/participants?conversation_id=' + self.cid, expected=403)
        # A typed nickname by itself is intentionally not an account mention.
        plain = self.send('@乙 just text', 'mention-plain')
        self.assertFalse(mentioned(plain, self.bob_id))

    def test_04_group_picker_finds_offline_members_and_checks_removal(self):
        cid = self.alice.request('/api/groups', {'action': 'create', 'name': '提及测试群'})['conversation_id']
        self.alice.request('/api/groups', {'action': 'invite', 'conversation_id': cid, 'user_id': self.bob_id})
        invite = next(i for i in self.bob.request('/api/bootstrap')['group_invites'] if i['cid'] == cid)
        self.bob.request('/api/groups', {'action': 'accept', 'invitation_id': invite['id']})
        self.bob.request('/api/groups', {'action': 'nickname', 'conversation_id': cid, 'nickname': '群内乙'})
        found = self.alice.request('/api/participants?conversation_id=' + cid + '&q=' + quote('群内乙'))['users']
        self.assertEqual(found[0]['id'], self.bob_id)
        self.assertEqual(found[0]['group_nickname'], '群内乙')
        self.alice.request('/api/groups', {'action': 'kick', 'conversation_id': cid, 'user_id': self.bob_id})
        self.alice.request('/api/messages', {'conversation_id': cid, 'client_id': 'mention-left', 'body': '@群内乙', 'mentions': [{'user_id': self.bob_id, 'start': 0, 'length': 4}]}, expected=400)

    def test_05_local_history_does_not_wait_for_offline_server(self):
        original = self.send('instant local history', 'instant-history')
        self.history_until(self.bob, lambda rows: any(m['id'] == original['id'] for m in rows))
        self.bob.request('/api/bootstrap')
        self.stop_process(self.__class__.server)
        try:
            started = time.monotonic()
            data = self.bob.request('/api/conversations/' + self.cid + '/messages?local=1')
            self.assertLess(time.monotonic() - started, 2)
            self.assertTrue(any(m['id'] == original['id'] for m in data['messages']))
            result = self.bob.request('/api/conversations/' + self.cid + '/messages?local=1&message_id=' + str(original['id']))
            self.assertEqual(result['message']['id'], original['id'])
            self.eve.request('/api/conversations/' + self.cid + '/messages?local=1', expected=403)
        finally:
            self.__class__.server = self.start_server()


class MentionNotificationTests(unittest.TestCase):
    def test_only_explicit_mentions_bypass_conversation_mute_and_history_stays_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            bootstrap = {'me': {'id': 'self'}, 'server_id': 'features', 'offline': True}
            context = SecureClient(directory, 'http://127.0.0.1:23074', '', bootstrap)
            context.vault.put('ui', 'muted', ['room:lobby', 'group:test'])
            notifications, enabled = [], [True]
            agent = BackgroundAgent(SimpleNamespace(contexts={}), directory, lambda: context.url, notifications.append, enabled=lambda: enabled[0])
            agent.activate(context, bootstrap)
            for cid in ('room:lobby', 'group:test'):
                normal = {'conversation_id': cid, 'sender': {'id': 'peer'}, 'body': '@同名', 'mentions': []}
                agent.received(context, normal)
                other = {**normal, 'mentions': [{'user_id': 'other', 'start': 0, 'length': 3}]}
                agent.received(context, other)
                self.assertEqual(notifications, [])
                target = {**normal, 'mentions': [{'user_id': 'self', 'start': 0, 'length': 3}]}
                for extra in ({'history_sync': True}, {'recalled': True}, {'sender': {'id': 'self'}}):
                    agent.received(context, {**target, **extra})
                self.assertEqual(notifications, [])
                enabled[0] = False
                agent.received(context, target)
                self.assertEqual(notifications, [])
                enabled[0] = True
                agent.received(context, target)
                self.assertEqual(notifications, [target])
                notifications.clear()

    def test_malformed_mentions_cannot_create_a_false_alert(self):
        for mentions in ([{'user_id': 'self', 'start': True, 'length': 2}], [{'user_id': 'self', 'start': 0, 'length': 99}], [{'user_id': 'self', 'start': 0, 'length': 3}] * 2):
            with self.assertRaises(ValueError):
                checked_mentions('@同名', mentions)
        self.assertFalse(mentioned({'body': '@self', 'mentioned': True}, 'self'))

    def test_retries_do_not_alert_twice_and_accounts_remain_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            context = SimpleNamespace(me={'id': 'self'}, vault=SimpleNamespace(get=lambda *args: []))
            notifications = []
            agent = BackgroundAgent(SimpleNamespace(), directory, lambda: '', notifications.append)
            agent.active = context
            message = {'id': 1, 'client_id': 'stable-id', 'conversation_id': 'group:test', 'sender': {'id': 'peer'}}
            for _ in range(3): agent.received(context, message)
            self.assertEqual(len(notifications), 1)
            agent.received(context, {**message, 'client_id': 'second'})
            self.assertEqual(len(notifications), 2)
            other = SimpleNamespace(me={'id': 'another-account'}, vault=context.vault)
            agent.active = other
            agent.received(context, {**message, 'client_id': 'stale-session'})
            agent.received(other, message)
            self.assertEqual(len(notifications), 3)


class NetworkWindowTests(unittest.TestCase):
    def test_rolling_window_reset_and_zero_samples(self):
        status = NetworkStatus()
        self.assertIsNone(status.snapshot()['loss_rate'])
        for _ in range(30): status.record(10.5, 'a')
        value = status.record(None, 'a')
        self.assertEqual(value['samples'], 30)
        self.assertEqual(value['loss_rate'], 3.3)
        self.assertIsNone(value['latency_ms'])
        value = status.record(22, 'b')
        self.assertEqual(value['samples'], 1)
        self.assertEqual(value['loss_rate'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
