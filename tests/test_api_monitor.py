import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode
from unittest.mock import Mock, patch

import bot
from api_monitor import ApiMonitor, baseline_path, payload_fields


BASELINE = Path(__file__).resolve().parents[1] / 'api_baseline.json'
PREFIX = 'https://www.douyu.com/japi/revenuenc/web/actgame/'


def end_body(**extra):
    return urlencode({'ctn': 'csrf-private', 'rid': 'room-private', 'battleId': 1,
        'battleToken': 'token-private', 'stage': 121, 'result': 2, 'curHp': 0,
        'timestamp': 1000, 'sign': 'sign-private', 'logIdList': '[1,2,3]',
        'stat': json.dumps({'totalDmg': 3000, 'cardDmg': {'123456': 1000, '987654': 500},
                           'skillDmg': 0, 'invalidDmg': 1500, **extra})})


class ApiMonitorTests(unittest.TestCase):
    def monitor(self):
        monitor = ApiMonitor(BASELINE)
        self.assertIsNone(monitor.error)
        monitor.arm('战斗')
        return monitor

    def test_normal_battle_variants_and_dynamic_card_ids(self):
        m = self.monitor()
        requests = [('POST', 'game/stageEnd', end_body()),
                    ('POST', 'game/nextStage', 'ctn=x&rid=1&battleId=2&battleToken=x&stage=20&skillId=0&timestamp=9&sign=x'),
                    ('POST', 'game/receiveBox', 'ctn=x&rid=1&timestamp=9'),
                    ('POST', 'game/receiveBox', 'ctn=x&rid=1'),
                    ('POST', 'userTask/receiveBox', 'ctn=x&rid=1'),
                    ('POST', 'basics/panelInfo', 'ctn=x&rid=1'),
                    ('GET', 'game/battleInfo?rid=1&battleToken=x', ''),
                    ('GET', 'userTask/weekPassTask?rid=1', ''),
                    ('GET', 'userTask/panelInfo?rid=1', '')]
        for _ in range(3):
            for method, endpoint, body in requests:
                self.assertIsNone(m.observe(PREFIX + endpoint, method, body), endpoint)
        self.assertEqual(len(m.recent), 16, 'history must stay bounded')

    def test_nested_fields_are_detected_and_report_is_redacted(self):
        m = self.monitor()
        incident = m.observe(PREFIX + 'game/stageEnd', 'POST', end_body(controlStats={'ice': 7}))
        self.assertIn('/stat/controlStats/ice', incident['differences'][0]['fields'])
        self.assertFalse(m.active)
        with tempfile.TemporaryDirectory() as tmp:
            path = m.write_report(tmp, {'rounds_done': 3}, True)
            text = Path(path).read_text(encoding='utf-8')
            report = json.loads(text)
        for secret in ('token-private', 'csrf-private', 'room-private', 'sign-private'):
            self.assertNotIn(secret, text)
        self.assertTrue(report['runtime_restored'])
        self.assertEqual(report['context']['rounds_done'], 3)
        self.assertEqual(report['incident']['request']['body']['data']['stat']['controlStats']['ice'], 7)

    def test_new_endpoint_method_host_and_query_field(self):
        cases = [(PREFIX + 'game/newReport', 'POST', '{}', 'new_endpoint'),
                 (PREFIX + 'game/stageEnd', 'PUT', end_body(), 'new_endpoint'),
                 (PREFIX.replace('www.douyu.com', 'other.example') + 'game/stageEnd', 'POST', end_body(), 'new_host'),
                 (PREFIX + 'game/stageEnd?attackSpeed=2', 'POST', end_body(), 'new_fields')]
        for url, method, body, kind in cases:
            with self.subTest(kind=kind):
                incident = self.monitor().observe(url, method, body)
                self.assertEqual(incident['differences'][0]['kind'], kind)

    def test_card_map_wildcard_does_not_hide_new_fields(self):
        for stats in ({'123': {'newMetric': 1}}, {'speed': 3}):
            m = self.monitor()
            raw = json.dumps({'stat': {'cardDmg': stats}})
            incident = m.observe(PREFIX + 'game/stageEnd', 'POST', raw)
            self.assertTrue(incident['differences'][0]['fields'])

    def test_paused_lobby_and_delayed_manual_requests_are_ignored(self):
        m = self.monitor()
        m.suspend()
        self.assertIsNone(m.observe(PREFIX + 'basics/openBag', 'POST', 'unknown=1'))
        manual_started = time.time() * 1000 - 1000
        m.arm('战斗')
        self.assertIsNone(m.observe(PREFIX + 'basics/openBag', 'POST', 'unknown=1', manual_started))
        self.assertIsNone(m.observe('https://example.com/analytics', 'POST', 'unknown=1'))
        self.assertIsNotNone(m.observe(PREFIX + 'game/newReport', 'POST', 'unknown=1'))

    def test_invalid_or_oversize_payload_cannot_pass_silently(self):
        for raw in ('binary-unknown-body', 'stat=%7Bbroken', 'stat=' + 'x' * (256 * 1024)):
            incident = self.monitor().observe(PREFIX + 'game/stageEnd', 'POST', raw)
            self.assertEqual(incident['differences'][0]['kind'], 'unreadable_payload')
        self.assertIn('/list/[]/new', payload_fields('list=%5B%7B%22new%22%3A1%7D%5D'))

    def test_external_baseline_is_preserved_and_invalid_file_stays_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = baseline_path(tmp)
            self.assertEqual(path.read_bytes(), BASELINE.read_bytes())
            path.write_text('{bad json', encoding='utf-8')
            self.assertEqual(baseline_path(tmp).read_text(), '{bad json')
            m = ApiMonitor(path)
            self.assertIsNotNone(m.error)
            m.arm('战斗')
            self.assertFalse(m.active)
            self.assertEqual(m.pending['kind'], 'baseline_error')

    def test_reload_uses_reviewed_external_file_without_auto_learning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = baseline_path(tmp)
            m = ApiMonitor(path)
            for _ in range(2):
                m.reload()
                m.arm('战斗')
                self.assertIsNotNone(m.observe(PREFIX + 'game/stageEnd', 'POST', end_body(newCounter=1)))
            template = json.loads(path.read_bytes())
            template['requests']['POST /japi/revenuenc/web/actgame/game/stageEnd']['body'].append('/stat/newCounter')
            path.write_text(json.dumps(template), encoding='utf-8')
            m.reload()
            m.arm('战斗')
            self.assertIsNone(m.observe(PREFIX + 'game/stageEnd', 'POST', end_body(newCounter=1)))

    def test_api_pause_restores_controls_keeps_counts_and_skips_stale_click(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bot.LOGDIR', tmp):
            b = bot.GuardFishBot(rounds=9)
            b._api_monitor = self.monitor()
            b.rounds_done = 3
            b._battle_active = b._runtime_script_loaded = True
            b._natural_end_requested = True
            b._page = Mock()
            b._page.evaluate.return_value = {'ok': True}
            b._close_browser = Mock()
            entered = threading.Event()
            b._log = lambda *args: entered.set() if b._paused else None
            b._api_monitor.observe(PREFIX + 'game/stageEnd', 'POST', end_body(newCounter=7))
            worker = threading.Thread(target=lambda: b._click('stale-click'), daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertFalse(b._runtime_script_loaded)
                self.assertFalse(b._natural_end_requested)
                self.assertFalse(b._api_monitor.active)
                self.assertEqual(b.rounds_done, 3)
                before = b._page.evaluate.call_count
                b._on_request(Mock(url=PREFIX + 'basics/openBag', method='POST', post_data='unknown=1'))
                self.assertEqual(before, b._page.evaluate.call_count)
                self.assertEqual(len(list(Path(tmp).glob('api_change_*.json'))), 1)
                self.assertTrue(b.request_resume())
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(b.rounds_done, 3)
                self.assertIsNone(b._api_monitor.pending)
                self.assertFalse(b._api_monitor.active, 'must first re-identify page before arming')
                scripts = [c.args[0] for c in b._page.evaluate.call_args_list]
                self.assertIn(bot.JS_HP_UNLOAD, scripts)
                self.assertNotIn('stale-click', scripts)
                self.assertNotIn(bot.JS_HP_END, scripts)
                b._close_browser.assert_not_called()
            finally:
                b.request_stop()
                worker.join(3)

    def test_scope_ends_at_lobby_and_keeps_stage_transition_active(self):
        b = bot.GuardFishBot()
        b._api_observe_state({'skillPanel': [1]})
        self.assertTrue(b._api_monitor.active)
        b._api_observe_state({'result': True})
        self.assertTrue(b._api_monitor.active)
        b._api_observe_state({'canChallenge': True})
        self.assertFalse(b._api_monitor.active)
        b._paused = True
        b._api_observe_state({'inBattle': True})
        self.assertFalse(b._api_monitor.active)
