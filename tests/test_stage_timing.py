import unittest

from stage_timing import StageTimer


def payload(stage, error=0):
    return {"error": error, "data": {"stageInfo": {"curStage": stage}}}


class StageTimingTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.timer = StageTimer(self.rows.append)

    def event(self, api, stage, t, error=0):
        self.timer.on_api("game/" + api, payload(stage, error), t, 1700000000 + t)

    def test_stage_end_waits_for_confirmed_progress_and_excludes_skill_wait(self):
        self.event("stageStart", 104, 10)
        self.event("stageEnd", 104, 55)
        self.assertEqual(self.rows, [])
        self.event("nextStage", 105, 60)
        self.event("stageStart", 105, 74)  # 选技能等待不计入正式战斗计时
        self.event("stageEnd", 105, 120)
        self.event("nextStage", 106, 123)
        self.assertEqual([r["duration_s"] for r in self.rows], [45, 46])
        self.assertTrue(all(not r["estimated"] for r in self.rows))

    def test_reward_duplicate_and_stale_responses_do_not_reset(self):
        self.event("stageStart", 104, 10)
        self.event("stageStart", 104, 20)
        self.event("receiveBox", 104, 30)
        self.event("stageEnd", 104, 50)
        self.event("stageEnd", 104, 55)
        self.event("nextStage", 105, 60)
        self.event("stageStart", 103, 61)
        self.assertEqual(self.rows[0]["duration_s"], 40)
        self.assertEqual(self.timer.current["stage"], 105)

    def test_failed_response_and_death_are_not_clears(self):
        self.event("stageStart", 104, 10)
        self.event("nextStage", 105, 50, error=1042100)
        self.event("stageEnd", 104, 55)
        self.timer.finish(60, 1700000060, "结算完成")
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.rows[0]["outcome"], "unfinished")

    def test_poll_fallback_is_partial_and_gaps_not_fabricated(self):
        self.timer.observe(104, 10, 1700000010)
        self.timer.observe(105, 70, 1700000070)
        self.timer.observe(108, 160, 1700000160)
        self.assertTrue(self.rows[0]["partial"] and self.rows[0]["estimated"])
        self.assertEqual(self.rows[1]["outcome"], "gap")
        self.assertEqual(self.rows[1]["duration_s"], 90)

    def test_missing_stage_start_falls_back_to_observation(self):
        self.event("stageStart", None, 10)
        self.timer.observe(104, 11, 1700000011)
        self.timer.observe(105, 61, 1700000061)
        self.assertEqual(self.rows[0]["duration_s"], 50)
        self.assertTrue(self.rows[0]["estimated"])

    def test_unnumbered_end_is_marked_inferred(self):
        self.event("stageStart", 104, 10)
        self.event("stageEnd", None, 55)
        self.event("nextStage", 105, 60)
        self.assertTrue(self.rows[0]["end_stage_inferred"])
        self.assertTrue(self.rows[0]["estimated"])


if __name__ == "__main__":
    unittest.main()
