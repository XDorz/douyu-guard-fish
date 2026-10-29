import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings


class SettingsTests(unittest.TestCase):
    def test_roundtrip_with_decimal_parameters_and_future_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            values = {**settings.DEFAULTS, "rounds": "9", "attack_multiplier": "2.5",
                      "one_hit_mode": "multiplier", "hp_guard": False, "one_hit": True,
                      "future_option": "ignored"}
            settings.save(path, values)
            got, error = settings.load(path)
            self.assertIsNone(error)
            self.assertEqual(got["rounds"], "9")
            self.assertEqual(got["attack_multiplier"], "2.5")
            self.assertFalse(got["hp_guard"])
            self.assertTrue(got["one_hit"])
            self.assertEqual(got["one_hit_mode"], "multiplier")
            self.assertNotIn("future_option", got)

    def test_invalid_numbers_and_incomplete_edits_keep_last_good_value(self):
        base = {**settings.DEFAULTS, "rounds": "9"}
        got = settings.normalize({"rounds": "", "one_hit_probability": "nan",
                                  "attack_multiplier": "inf", "hp_guard": "false",
                                  "gap_min": -1, "hp_threshold": 1.5}, base)
        self.assertEqual(got, base)

    def test_corrupt_configuration_does_not_break_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{broken", encoding="utf-8")
            got, error = settings.load(path)
            self.assertEqual(got, settings.DEFAULTS)
            self.assertIsNotNone(error)

    def test_failed_save_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            settings.save(path, settings.DEFAULTS)
            original = path.read_bytes()
            with patch("settings.os.replace", side_effect=OSError("test failure")):
                with self.assertRaises(OSError):
                    settings.save(path, {**settings.DEFAULTS, "rounds": "9"})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
