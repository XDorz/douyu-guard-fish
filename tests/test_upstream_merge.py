import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import bot
from activity_budget import ActivityBudget
from browser_compat import NetworkState, evaluate_main, is_stats_beacon
import settings


class MergeTests(unittest.TestCase):
    def test_main_world_handles_both_patchright_versions_and_playwright(self):
        class Modern:
            __module__ = 'patchright.sync_api._generated'
            def evaluate(self, js, arg=None, *, isolated_context=True):
                return js, arg, isolated_context

        class Legacy:
            __module__ = 'patchright.sync_api._generated'
            def evaluate(self, js, arg=None, *, isolatedContext=True):
                return js, arg, isolatedContext

        for page in (Modern(), Legacy()):
            self.assertEqual(evaluate_main(page, 'script', {'on': True}),
                             ('script', {'on': True}, False))
        page = Mock()
        page.evaluate.side_effect = TypeError('script failed')
        with self.assertRaises(TypeError):
            evaluate_main(page, 'side-effect')
        page.evaluate.assert_called_once_with('side-effect')

    def test_response_cache_replaces_missing_page_hook_and_rejects_errors(self):
        cache = NetworkState()
        cache.observe('basics/panelInfo', {'error': 0, 'data': {
            'userInfo': {'nn': 'tester', 'freeNum': 2, 'battleRound': 28},
            'siteUserInfo': {'coin': 10}}})
        cache.observe('game/battleInfo', {'error': 0, 'data': {
            'battleInfo': {'curStage': 121, 'baseHp': 20}}})
        cache.observe('game/stageStart', {'error': 0, 'data': {
            'stageInfo': {'curStage': 122, 'curHp': 1}, 'status': 10}})
        cache.observe('game/stageEnd', {'error': 1042008, 'data': {'status': 20}})
        state = cache.merge({'inBattle': True, 'wm': {'freeNum': None}})
        self.assertEqual(state['wm']['stage'], {'curStage': 122, 'curHp': 1})
        self.assertFalse(state['wm']['battleOver'])
        self.assertTrue(state['wm']['panelReady'])
        self.assertEqual(state['wm']['battleRound'], 28)
        self.assertEqual(bot.GuardFishBot._coin_balance(state), 10)
        cache.observe('game/stageEnd', {'error': 0, 'data': {'status': 20}})
        self.assertTrue(cache.values['battleOver'])
        cache.observe('game/battleStart', {'error': 0, 'data': {'userInfo': {'freeNum': 0}}})
        self.assertFalse(cache.values['battleOver'])
        self.assertEqual(cache.values['freeNum'], 0)
        fresh = NetworkState()
        fresh.observe('basics/panelInfo', {'code': 1002})
        self.assertFalse(fresh.values['panelReady'])
        self.assertNotIn('stage', fresh.values)

    def test_telemetry_filter_cannot_block_game_reports_or_other_hosts(self):
        b = bot.GuardFishBot()
        for url, blocked in (
            ('https://dotserver.douyucdn.cn/fish3/1.gif?a=1', True),
            ('https://www.douyu.com/japi/revenuenc/web/actgame/game/stageEnd', False),
            ('https://other.example/fish3/1.gif', False),
            ('https://dotserver.douyucdn.cn/fish3/1.gif/other', False),
        ):
            route = Mock()
            route.request.url = url
            self.assertEqual(is_stats_beacon(url), blocked)
            b._on_stats_beacon(route)
            self.assertEqual(route.fulfill.called, blocked)
            self.assertEqual(route.continue_.called, not blocked)

    def test_activity_pauses_and_rest_deadline_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp, patch('activity_budget.time.monotonic') as mono, \
                patch('activity_budget.time.time', return_value=1000):
            path = str(Path(tmp) / 'active_state.json')
            budget = ActivityBudget(path)
            mono.return_value = 100
            budget.start()
            mono.return_value = 160
            budget.stop()
            self.assertEqual(budget.seconds, 60)
            mono.return_value = 1000  # 暂停的时间不累计。
            budget.start()
            mono.return_value = 1060
            budget.stop()
            self.assertEqual(budget.seconds, 120)
            self.assertTrue(budget.due(2))
            self.assertFalse(budget.due(0))
            budget.begin_rest(90)
            loaded = ActivityBudget(path)
            self.assertEqual(loaded.rest_until, 6400)
            loaded.begin_rest(90)
            self.assertEqual(loaded.rest_until, 6400)
            loaded.finish_rest()
            self.assertFalse(ActivityBudget(path).due(2))
            old = {'date': '20000101', 'sec': 99999}
            Path(path).write_text(json.dumps(old))
            self.assertEqual(ActivityBudget(path).seconds, 0)

    def test_existing_options_survive_new_defaults(self):
        old = {'rounds': '9', 'hp_guard': False, 'one_hit': True,
               'one_hit_mode': 'multiplier', 'attack_multiplier': '3.5',
               'max_minutes': '17', 'combat': {'repeat_ice': True}}
        config = settings.normalize(old)
        for key, value in old.items():
            if key != 'combat':
                self.assertEqual(config[key], value)
        self.assertTrue(config['combat']['repeat_ice'])
        self.assertEqual(config['daily_limit_min'], '240')
        self.assertTrue(config['random_skills'])
        self.assertEqual(settings.normalize({'one_hit_mode': 'gentle'})['one_hit_mode'], 'gentle')

    def test_rest_does_not_count_a_round_and_can_stop(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bot.DATA_DIR', tmp):
            b = bot.GuardFishBot(rounds=9)
            b._log = Mock()
            b._activity.seconds = 14400
            b.rounds_done = 3
            b._stop.set()
            self.assertFalse(b._long_rest())
            self.assertEqual(b.rounds_done, 3)
            self.assertGreater(b._activity.rest_until, 0)
            self.assertFalse(b._api_monitor.active)


if __name__ == '__main__':
    unittest.main()
