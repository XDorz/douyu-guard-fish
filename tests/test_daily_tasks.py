import unittest
from unittest.mock import Mock

import bot
from daily_tasks import DailyChallenge


class DailyTaskTests(unittest.TestCase):
    def test_matches_daily_challenge_by_config_not_task_order_or_battle_round(self):
        daily = DailyChallenge()
        daily.update_config({'data': {'user_task': {'dayTasks': [
            {'taskId': 90, 'taskType': 4, 'cycle': 2, 'switchFlag': 1, 'count': 10},
            {'taskId': 92, 'taskType': 4, 'cycle': 2, 'switchFlag': 1, 'count': 30},
            {'taskId': 99, 'taskType': 3, 'cycle': 2, 'switchFlag': 1, 'count': 30},
        ]}}})
        daily.update_status({'error': 0, 'data': {'dayTasks': [
            {'taskId': 99, 'num': 29}, {'taskId': 90, 'num': 10},
            {'taskId': '92', 'num': '28', 'taskStatus': 1},
        ]}})
        self.assertEqual(daily.progress(), {'count': 28, 'target': 30, 'task_id': 92})
        daily.update_status({'error': 1002, 'data': {'dayTasks': []}})
        self.assertEqual(daily.progress()['count'], 28)
        daily.update_status({'data': {'dayTasks': [{'taskId': 92, 'num': 0}]}})
        self.assertEqual(daily.progress()['count'], 0, 'server day reset may decrease the count')
        daily.update_status({'data': {'dayTasks': [{'taskId': 92, 'num': 'unknown'}]}})
        self.assertIsNone(daily.progress())

    def test_unknown_task_is_not_inferred_from_server_battle_counter(self):
        b = bot.GuardFishBot()
        b._sync_status({'wm': {'battleRound': 28, 'freeNum': 6}})
        self.assertEqual(b.status['battleRound'], 28)
        self.assertIsNone(b.status['daily_count'])

    def test_queries_are_throttled_and_pausing_does_not_touch_page(self):
        b = bot.GuardFishBot()
        b._page = Mock()
        b._page.evaluate.return_value = None
        b._refresh_daily_progress()
        b._refresh_daily_progress()
        b._page.evaluate.assert_called_once()
        b._paused = True
        b._refresh_daily_progress(force=True)
        b._page.evaluate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
