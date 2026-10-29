import os
import tempfile
import unittest
from unittest.mock import patch

from yu_gui import App, tk
from bot import GuardFishBot
import settings


class GuiConfigTests(unittest.TestCase):
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
