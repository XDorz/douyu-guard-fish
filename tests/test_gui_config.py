import os
import tempfile
import unittest
from unittest.mock import patch

from yu_gui import App, tk
import settings


class GuiConfigTests(unittest.TestCase):
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
