import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

import bot


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class BattleControlTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for runtime JS checks')
    def test_runtime_damage_and_natural_end(self):
        scripts = dict(watch=bot.JS_HP_WATCH, cfg=bot.JS_HP_CFG,
                       end=bot.JS_HP_END, unload=bot.JS_HP_UNLOAD)
        result = subprocess.run(
            [shutil.which('node'), str(Path(__file__).with_name('runtime_controls.cjs'))],
            input=json.dumps(scripts), text=True, encoding='utf-8',
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def make_bot(self, **kwargs):
        b = bot.GuardFishBot(gap_min=0, gap_max=0, **kwargs)
        for name in ('_ensure_browser', '_close_browser', '_shutdown_pw', '_log',
                     '_battle_write', '_debug_write', '_debug_capture', '_stage_record'):
            setattr(b, name, Mock())
        b._goto_game = Mock(return_value=True)
        b._page = Mock()
        b._page.evaluate.return_value = None
        return b

    def wait_for_pause(self, b):
        entered = threading.Event()
        original_log = b._log

        def log(line, *args):
            original_log(line, *args)
            if b._paused:
                entered.set()
        b._log = log
        return entered

    def test_pause_preserves_worker_browser_and_rounds_including_last_round(self):
        for rounds in (1, 3):
            with self.subTest(rounds=rounds), patch('bot.ensure_dirs'):
                b = self.make_bot(rounds=rounds)
                b._state = Mock(return_value={'canChallenge': True})
                visited = []

                def battle(idx):
                    visited.append(idx)
                    if idx == 1:
                        b.request_pause()
                    return {'ended': '结算完成', 'dur': 10}
                b._one_battle = Mock(side_effect=battle)
                entered = self.wait_for_pause(b)
                b.start()
                worker, page = b._thread, b._page
                try:
                    self.assertTrue(entered.wait(3), 'worker did not enter pause')
                    self.assertEqual(b.rounds_done, 1)
                    self.assertEqual(b.status['rounds_done'], 1)
                    self.assertEqual(visited, [1])
                    self.assertTrue(worker.is_alive())
                    self.assertIs(b._page, page)
                    b._close_browser.assert_not_called()
                    calls = b._state.call_count
                    self.assertFalse(b.request_pause())
                    self.assertEqual(b._state.call_count, calls)
                    self.assertTrue(b.request_resume())
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertIs(b._thread, worker)
                    self.assertEqual(visited, list(range(1, rounds + 1)))
                    self.assertEqual(b.rounds_done, rounds)
                    self.assertEqual(b._battle_write.call_count, rounds)
                    b._ensure_browser.assert_called_once()
                    b._close_browser.assert_called_once()
                finally:
                    b.request_stop()
                    worker.join(3)

    def test_stop_wakes_paused_worker_without_starting_another_battle(self):
        with patch('bot.ensure_dirs'):
            b = self.make_bot(rounds=9)
            b._state = Mock(return_value={'canChallenge': True})
            b._one_battle = Mock(side_effect=lambda idx: (b.request_pause() and {'dur': 1}))
            entered = self.wait_for_pause(b)
            b.start()
            try:
                self.assertTrue(entered.wait(3))
                b.request_stop()
                b._thread.join(3)
                self.assertFalse(b._thread.is_alive())
                b._one_battle.assert_called_once_with(1)
                self.assertEqual(b.rounds_done, 1)
                b._close_browser.assert_called_once()
            finally:
                b.request_stop()
                b._thread.join(3)

    def battle_harness(self, clock, **kwargs):
        b = self.make_bot(**kwargs)
        b._try_start_battle = Mock(return_value=(True, {'skills': []}))
        b._sync_status = Mock()
        b._finish_stage_timing = Mock(return_value=[])
        b._apply_runtime_controls = Mock()
        b._snap = Mock()
        return b

    def test_slow_wave_does_not_refresh_or_finish_before_advance_target(self):
        for mode in ('off', 'kills', 'waves'):
            with self.subTest(mode=mode):
                clock = FakeClock()
                b = self.battle_harness(clock, advance_stages=2, max_battle_seconds=1000)

                def state():
                    elapsed = clock.now - 1000
                    stage = 121 if elapsed < 400 else (122 if elapsed < 450 else 123)
                    lobby = b._natural_end_requested and elapsed > 451
                    return {'inBattle': not lobby, 'canChallenge': lobby,
                            'wm': {'stage': {'curStage': stage, 'curHp': 20}}}

                def evaluate(js, *args):
                    elapsed = clock.now - 1000
                    if js == bot.JS_HP_DRAIN and mode != 'off':
                        return {'oneHitKills': int(elapsed / 7) if mode == 'kills' else 0,
                                'progress': [int(elapsed / 20)] if mode == 'waves' else None}
                    return None
                b._state = Mock(side_effect=state)
                b._page.evaluate.side_effect = evaluate
                with patch('bot.time.time', clock.time), patch('bot.time.sleep', clock.sleep):
                    result = b._one_battle(1)
                self.assertEqual(result['stage0'], 121)
                self.assertEqual(result['stageN'], 123)
                self.assertGreater(result['dur'], 450)
                self.assertEqual(b._stage_target, 123)
                self.assertIn('123', b._natural_end_reason)
                b._page.reload.assert_not_called()

    def test_pause_request_during_battle_uses_natural_end(self):
        clock = FakeClock()
        b = self.battle_harness(clock)
        b.request_pause()

        def state():
            lobby = b._natural_end_requested and clock.now > 1001
            return {'inBattle': not lobby, 'canChallenge': lobby,
                    'wm': {'stage': {'curStage': 121, 'curHp': 20}}}
        b._state = Mock(side_effect=state)
        with patch('bot.time.time', clock.time), patch('bot.time.sleep', clock.sleep):
            result = b._one_battle(1)
        self.assertIsNotNone(result)
        self.assertEqual(b._natural_end_reason, '暂时停止')
        self.assertTrue(b._pause_requested, 'run loop must pause after recording this result')
        b._close_browser.assert_not_called()

    def test_reward_modal_is_not_a_completed_battle_and_result_waits_for_lobby(self):
        clock = FakeClock()
        b = self.battle_harness(clock)
        closed = set()

        def state():
            elapsed = clock.now - 1000
            reward = elapsed >= 2 and 'reward' not in closed
            result = elapsed >= 8 and 'result' not in closed
            lobby = elapsed >= 13 and 'result' in closed
            return {'inBattle': elapsed < 8, 'canChallenge': lobby,
                    'result': reward or result, 'treasure': reward,
                    'wm': {'battleOver': elapsed >= 8,
                           'stage': {'curStage': 121, 'curHp': 20}}}

        def click(js, *args):
            if js == bot.JS_RESULT_TEXT:
                return '奖励' if clock.now < 1008 else '真正结算'
            if js == bot.JS_RESULT_BTN:
                closed.add('reward' if clock.now < 1008 else 'result')
                return 'closed'
        b._state = Mock(side_effect=state)
        b._click = Mock(side_effect=click)
        with patch('bot.time.time', clock.time), patch('bot.time.sleep', clock.sleep):
            result = b._one_battle(1)
        self.assertEqual(closed, {'reward', 'result'})
        self.assertEqual(result['resultText'], '真正结算')
        self.assertEqual(result['ended'], '结算完成')
        self.assertGreaterEqual(result['dur'], 13)

    def test_grace_period_polls_and_resume_keeps_original_stage_target(self):
        clock = FakeClock()
        b = self.battle_harness(clock, advance_stages=2, max_battle_seconds=2, pause_on_timeout=True)
        resumed = False
        pause_times = []

        def resume(**kwargs):
            nonlocal resumed
            pause_times.append(clock.now)
            self.assertTrue(kwargs['unfinished'])
            self.assertEqual(b.rounds_done, 0)
            self.assertEqual(b._stage_target, 123)
            resumed = True
            b._pause_requested = False
            return True

        b._wait_if_paused = Mock(side_effect=resume)
        b._page.evaluate.side_effect = lambda js, *args: (
            {'oneHitKills': 0 if resumed else 40} if js == bot.JS_HP_DRAIN else None)
        b._state = Mock(side_effect=lambda: {
            'inBattle': not resumed, 'canChallenge': resumed,
            'wm': {'stage': {'curStage': 122 if resumed else 121, 'curHp': 20}}})
        with patch('bot.time.time', clock.time), patch('bot.time.sleep', clock.sleep):
            result = b._one_battle(1)
        self.assertEqual(len(pause_times), 1)
        self.assertGreaterEqual(pause_times[0], 1182)
        self.assertLess(pause_times[0], 1184)
        self.assertGreater(b._state.call_count, 360, 'grace must keep polling every 0.5s')
        self.assertEqual(result['stage0'], 121)
        self.assertEqual(result['stageN'], 122)
        self.assertEqual(result['oneHitKills'], 40, 'resume must not erase battle counters')
        self.assertEqual(b._stage_target, 123)
        b._try_start_battle.assert_called_once()
        b._page.reload.assert_not_called()

    def test_cli_grace_timeout_does_not_count_unfinished_battle(self):
        clock = FakeClock()
        b = self.battle_harness(clock, rounds=9, max_battle_seconds=2)
        b._state = Mock(return_value={'inBattle': True, 'wm': {'stage': {'curStage': 121}}})
        with patch('bot.ensure_dirs'), patch('bot.time.time', clock.time), patch('bot.time.sleep', clock.sleep):
            b._run()
        self.assertEqual(b.rounds_done, 0)
        b._battle_write.assert_not_called()
        b._page.reload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
