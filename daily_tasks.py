"""每日挑战任务进度；按公开配置中的任务 ID 匹配接口，不推算本机场次。"""
import math


def _count(value):
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 and number.is_integer() else None
    except (TypeError, ValueError, OverflowError):
        return None


class DailyChallenge:
    def __init__(self):
        self.tasks = {}
        self.rows = []

    def update_config(self, payload):
        data = payload.get('data', {}) if isinstance(payload, dict) else {}
        tasks = (data.get('user_task') or {}).get('dayTasks') or []
        selected = {}
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id, count = _count(task.get('taskId')), _count(task.get('count'))
            if (_count(task.get('taskType')) == 4 and _count(task.get('cycle')) == 2
                    and _count(task.get('switchFlag')) == 1 and task_id is not None and count):
                selected[task_id] = count
        if selected:
            self.tasks = selected

    def update_status(self, payload):
        if not isinstance(payload, dict) or _count(payload.get('error', 0)) != 0:
            return
        rows = (payload.get('data') or {}).get('dayTasks')
        if isinstance(rows, list):
            self.rows = rows

    def progress(self):
        if not self.tasks:
            return None
        # 使用最高档每日挑战任务；当前公开配置为 10/20/30 场。
        task_id = max(self.tasks, key=self.tasks.get)
        for row in self.rows:
            if isinstance(row, dict) and _count(row.get('taskId')) == task_id:
                count = _count(row.get('num'))
                if count is not None:
                    return {'count': count, 'target': self.tasks[task_id], 'task_id': task_id}
        return None


JS_DAILY_TASK_QUERY = r"""
async ({rid, config}) => {
  const read = async url => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch(url, {credentials: 'same-origin', signal: controller.signal});
      return response.ok ? await response.json() : null;
    } catch (_) { return null; }
    finally { clearTimeout(timer); }
  };
  const [tasks, settings] = await Promise.all([
    read('/japi/revenuenc/web/actgame/userTask/panelInfo?rid=' + encodeURIComponent(rid)),
    config ? read('https://wconf.douyucdn.cn/resource/common/activity/actgame_w.json') : null
  ]);
  const dayTasks = settings && settings.data && settings.data.user_task && settings.data.user_task.dayTasks;
  return {tasks, config: Array.isArray(dayTasks) ? {data: {user_task: {dayTasks:
    dayTasks.filter(t => Number(t.taskType) === 4).map(t => ({taskId: t.taskId, count: t.count,
      taskType: t.taskType, cycle: t.cycle, switchFlag: t.switchFlag}))}}} : null};
}
"""
