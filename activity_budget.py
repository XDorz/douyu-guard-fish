"""上游活跃时长休息机制；仅累计助手实际监控对局的时间。"""
import datetime
import json
import math
import os
import time


class ActivityBudget:
    def __init__(self, path):
        self.path = path
        self.date = self._today()
        self.seconds = 0.0
        self.rest_until = 0.0
        self.started = None
        try:
            with open(path, encoding='utf-8') as stream:
                data = json.load(stream)
            if data.get('date') == self.date:
                self.seconds = self._number(data.get('sec'))
            self.rest_until = self._number(data.get('rest_until'))
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    @staticmethod
    def _today():
        return datetime.date.today().strftime('%Y%m%d')

    @staticmethod
    def _number(value):
        result = float(value or 0)
        return max(0, result) if math.isfinite(result) else 0

    def start(self):
        if self.started is None:
            self.started = time.monotonic()

    def checkpoint(self):
        if self.date != self._today():
            self.date, self.seconds = self._today(), 0.0
            # 不把午夜前累计的片段算入新一天。
            if self.started is not None:
                self.started = time.monotonic()
        if self.started is not None:
            now = time.monotonic()
            self.seconds += max(0, now - self.started)
            self.started = now
        self.save()

    def stop(self):
        self.checkpoint()
        self.started = None

    def due(self, limit_minutes):
        return limit_minutes > 0 and (self.rest_until > 0 or self.seconds >= limit_minutes * 60)

    def begin_rest(self, minutes):
        if self.rest_until <= 0:
            self.rest_until = time.time() + max(1, minutes) * 60
            self.save()

    def finish_rest(self):
        self.seconds, self.rest_until = 0.0, 0.0
        self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path + '.tmp', 'w', encoding='utf-8') as stream:
                json.dump({'date': self.date, 'sec': round(self.seconds, 1),
                           'rest_until': self.rest_until}, stream)
            os.replace(self.path + '.tmp', self.path)
        except OSError:
            pass
