import os
import tempfile
import unittest
from unittest.mock import patch

from yu_gui import App, tk
from bot import GuardFishBot
import settings


class GuiConfigTests(unittest.TestCase):
    def test_combat_settings_apply_live_clamp_and_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('yu_gui.botmod.CONFIG_PATH', os.path.join(directory, 'config.json')), \
                    patch('yu_gui.botmod.chrome_candidates', return_value=[]):
                try:
                    root = tk.Tk()
                except tk.TclError as exc:
                    self.skipTest('GUI requires a display: %s' % exc)
                root.withdraw()
                app = App(root)
                app.bot, app.running = GuardFishBot(), True
                w = app.combat_widgets
                speed = w.groups['attack_speed']
                wind = w.groups['wind']
                speed['enabled'].set(True)
                speed['value'].set('-2')
                wind['enabled'].set(True)
                wind['mode'].set('固定')
                w._mode_changed('wind')
                wind['value'].set('37.5')
                wind['cd'].set('-1')
                w.repeat_ice.set(True)
                w.repeat_earth.set(True)
                w.flush()
                self.assertEqual(wind['cd'].get(), '0.5')
                self.assertEqual(app.bot.combat_controls['attack_speed']['multiplier'], -2)
                self.assertEqual(app.bot.combat_controls['effects']['wind']['probability'], 37.5)
                self.assertTrue(app.bot.combat_controls['repeat_ice'])
                wind['mode'].set('倍率')
                w._mode_changed('wind')
                wind['value'].set('1.5')
                w.flush()
                root.update_idletasks()
                app.running = False
                app.on_close()
                root = tk.Tk()
                root.withdraw()
                app = App(root)
                w = app.combat_widgets
                try:
                    self.assertEqual(w.groups['attack_speed']['value'].get(), '-2')
                    self.assertEqual(w.groups['wind']['value'].get(), '1.5')
                    self.assertEqual(w.groups['wind']['cd'].get(), '0.5')
                    self.assertTrue(w.repeat_ice.get() and w.repeat_earth.get())
                    w.groups['wind']['mode'].set('固定')
                    w._mode_changed('wind')
                    self.assertEqual(w.groups['wind']['value'].get(), '37.5')
                finally:
                    root.update_idletasks()
                    app.on_close()

    def test_pause_button_changes_to_resume_and_keeps_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('yu_gui.botmod.CONFIG_PATH', os.path.join(directory, 'config.json')), \
                    patch('yu_gui.botmod.chrome_candidates', return_value=[]):
                try:
                    root = tk.Tk()
                except tk.TclError as exc:
                    self.skipTest('GUI requires a display: %s' % exc)
                root.withdraw()
                app = App(root)
                b = GuardFishBot(rounds=9)
                b._log = lambda *args: None
                app.bot, app.running = b, True
                try:
                    app._refresh_run_state()
                    app.pause_btn.invoke()
                    self.assertTrue(b._pause_requested)
                    self.assertEqual(str(app.pause_btn['state']), 'disabled')
                    b.rounds_done = 1
                    b.status.update(rounds_done=1, round=1, state='已暂停')
                    b._paused = True
                    app._refresh_run_state()
                    self.assertEqual(app.pause_btn['text'], '恢复战斗')
                    self.assertIn('已完成 1/9', app.status.get())
                    app.pause_btn.invoke()
                    self.assertFalse(b._pause_requested)
                    self.assertTrue(b._pause_wakeup.is_set())
                    self.assertIs(app.bot, b)
                    self.assertEqual(b.rounds_done, 1)
                finally:
                    root.update_idletasks()
                    app.running = False
                    app.on_close()

    def test_reopen_restores_configuration_for_normal_and_debug_build(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with patch("yu_gui.botmod.CONFIG_PATH", path), patch("yu_gui.botmod.chrome_candidates", return_value=[]):
                try:
                    root = tk.Tk()
                except tk.TclError as exc:
                    self.skipTest("GUI requires a display: %s" % exc)
                root.withdraw()
                app = App(root)
                app.rounds.set("9")
                app.hp_var.set(False)
                app.one_hit_var.set(True)
                app.advance_stages.set("15")
                app.one_hit_mode.set("概率秒杀")
                app._one_hit_mode_changed()
                app.one_hit_value.set("37.5")
                app.one_hit_mode.set("攻击倍率")
                app._one_hit_mode_changed()
                app.one_hit_value.set("2.5")
                root.update_idletasks()
                app.on_close()

                for debug in (False, True):
                    root = tk.Tk()
                    root.withdraw()
                    app = App(root, debug=debug)
                    self.assertEqual(app.rounds.get(), "9")
                    self.assertEqual(app.advance_stages.get(), "15")
                    self.assertFalse(app.hp_var.get())
                    self.assertTrue(app.one_hit_var.get())
                    self.assertEqual(app.one_hit_value.get(), "2.5")
                    self.assertEqual(app._one_hit_mode_key(), "multiplier")
                    app.one_hit_mode.set("概率秒杀")
                    app._one_hit_mode_changed()
                    self.assertEqual(app.one_hit_value.get(), "37.5")
                    app.one_hit_mode.set("攻击倍率")
                    app._one_hit_mode_changed()
                    app.on_close()
                self.assertEqual(settings.load(path)[0]["attack_multiplier"], "2.5")


if __name__ == "__main__":
    unittest.main()
