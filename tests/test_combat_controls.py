import io
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock

import bot
from combat_controls import (ActivityWatch, KillStats, JS_COMBAT_INSTALL,
                             JS_COMBAT_SNAPSHOT, default_controls, normalize_controls)


class CombatControlTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for combat JS checks')
    def test_live_game_adapters(self):
        scripts = {'watch': bot.JS_HP_WATCH, 'install': JS_COMBAT_INSTALL,
                   'cfg': bot.JS_HP_CFG, 'snapshot': JS_COMBAT_SNAPSHOT,
                   'end': bot.JS_HP_END, 'unload': bot.JS_HP_UNLOAD,
                   'defaults': default_controls()}
        result = subprocess.run([shutil.which('node'), str(Path(__file__).with_name('combat_controls.cjs'))],
                                input=json.dumps(scripts), text=True, encoding='utf-8',
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_clamps_modes_cds_negative_speed_and_incomplete_inputs(self):
        values = default_controls()
        values['attack_speed'].update(enabled=True, multiplier=-2, fixed=-10)
        values['effects']['wind'].update(probability=200, cd=-8)
        values['effects']['ice'].update(cd=999)
        fixed = normalize_controls(values)
        self.assertEqual(fixed['attack_speed']['multiplier'], -2)
        self.assertEqual(fixed['attack_speed']['fixed'], .2)
        self.assertEqual(fixed['effects']['wind']['probability'], 100)
        self.assertEqual(fixed['effects']['wind']['cd'], .5)
        self.assertEqual(fixed['effects']['ice']['cd'], 180)
        self.assertEqual(normalize_controls({'attack_speed': {'multiplier': '-'}}, fixed), fixed)
        self.assertEqual(normalize_controls({'attack_speed': {'multiplier': float('nan')}}, fixed), fixed)
        self.assertEqual(normalize_controls({'attack_speed': {'multiplier': 0}})['attack_speed']['multiplier'], 0)
        self.assertEqual(normalize_controls({'attack_speed': {'multiplier': -.5}})['attack_speed']['multiplier'], -1)

    def test_kills_merge_without_duplicates_across_reload_and_stage_changes(self):
        stats = KillStats()
        stats.consume({'epoch': 1, 'stages': [{'stage': 121, 'kills': 10}]})
        stats.consume({'epoch': 1, 'stages': [{'stage': 121, 'kills': 11}, {'stage': 122, 'kills': 0}]})
        stats.consume({'epoch': 1, 'stages': [{'stage': 121, 'kills': 11}]})
        stats.consume({'epoch': 2, 'stages': [{'stage': 122, 'kills': 3, 'partial': True}]})
        self.assertEqual(stats.total, 14)
        self.assertEqual(stats.for_stage(121), {'kills': 11, 'kills_partial': False})
        self.assertEqual(stats.for_stage(122), {'kills': 3, 'kills_partial': True})
        self.assertIsNone(stats.for_stage(123)['kills'])

    def test_stage_log_collects_final_kill_after_api_callback(self):
        b = bot.GuardFishBot()
        b._stage_fp = io.StringIO()
        b._log = Mock()
        record = {'stage': 121, 'duration_s': 62.4, 'outcome': 'cleared', 'partial': False,
                  'estimated': False, 'end_stage_inferred': False}
        b._kills.consume({'epoch': 1, 'stages': [{'stage': 121, 'kills': 50}]})
        b._stage_record(record)
        self.assertEqual(b._stage_fp.getvalue(), '', 'do not write stale kill count inside response callback')
        b._kills.consume({'epoch': 1, 'stages': [{'stage': 121, 'kills': 51}]})
        b._flush_stage_records()
        written = json.loads(b._stage_fp.getvalue())
        self.assertEqual(written['kills'], 51)
        self.assertEqual(record['kills'], 51, 'battle report uses the enriched stage record too')
        self.assertIn('本关杀敌 51', b._log.call_args[0][0])

    def sample(self, shots=0, kills=0, tick=0, cooling=False):
        return {'epoch': 1, 'totalKills': kills, 'current': {
            'id': 1, 'projectiles': shots, 'tick': tick, 'running': True,
            'activityAvailable': True, 'cooling': cooling}}

    def test_watchdog_uses_projectiles_or_kills_even_without_stage_change(self):
        for kind in ('shots', 'kills'):
            watch = ActivityWatch()
            for second in range(800):
                sample = self.sample(**{kind: second // 200}, tick=second * 1000)
                self.assertFalse(watch.observe(sample, second))
        watch = ActivityWatch()
        for second in range(240):
            self.assertFalse(watch.observe(self.sample(tick=second * 1000), second))
        self.assertTrue(watch.observe(self.sample(tick=240000), 240))

    def test_watchdog_excludes_ui_pauses_missing_data_and_deliberately_slow_attacks(self):
        watch = ActivityWatch()
        for second in range(400):
            self.assertFalse(watch.observe(self.sample(tick=second * 1000, cooling=True), second))
        for second in range(400, 639):
            self.assertFalse(watch.observe(self.sample(tick=399000, cooling=True), second))
        self.assertTrue(watch.observe(self.sample(tick=399000, cooling=True), 639),
                        'frozen engine during a cooldown still needs recovery')
        self.assertFalse(watch.observe(self.sample(), 641, suspended=True))
        self.assertFalse(watch.observe(None, 1000))
        self.assertFalse(watch.observe(self.sample(), 1001))


if __name__ == '__main__':
    unittest.main()
