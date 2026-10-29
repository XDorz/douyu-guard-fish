"""单关耗时观察器。只统计响应/关卡切换，不参与游戏控制或场数判断。"""
import datetime


def stage_number(value):
    try:
        if isinstance(value, bool):
            return None
        num = int(value)
        return num if num > 0 and float(value) == num else None
    except (TypeError, ValueError, OverflowError):
        return None


def api_stage(payload):
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    for obj in (data.get("stageInfo"), data.get("battleInfo"), data):
        if isinstance(obj, dict):
            num = stage_number(obj.get("curStage"))
            if num is not None:
                return num
    return None


def api_ok(payload):
    return (isinstance(payload, dict)
            and payload.get("error") in (None, 0, "0")
            and payload.get("code") in (None, 0, "0", 200, "200"))


class StageTimer:
    def __init__(self, emit):
        self.emit = emit
        self.current = None
        self.records = []

    def _begin(self, stage, now, wall, source, partial=False):
        self.current = {"stage": stage, "mono": now, "wall": wall,
                        "source": source, "end": None, "partial": partial}

    def _record(self, now, wall, outcome, end_source, next_stage=None):
        cur = self.current
        end = cur["end"]
        inferred_end_stage = False
        # stageEnd 只有随后确实进入下一关，才作为通关结束时间。
        if outcome == "cleared" and end is not None:
            now, wall, end_source, inferred_end_stage = end
        exact_pair = (cur["source"] == "game/stageStart" and end_source == "game/stageEnd"
                      and not inferred_end_stage)
        record = {
            "stage": cur["stage"], "next_stage": next_stage,
            "start_at": datetime.datetime.fromtimestamp(cur["wall"]).isoformat(timespec="milliseconds"),
            "end_at": datetime.datetime.fromtimestamp(wall).isoformat(timespec="milliseconds"),
            "duration_s": round(max(0.0, now - cur["mono"]), 3),
            "outcome": outcome, "start_source": cur["source"], "end_source": end_source,
            "estimated": not exact_pair,
            "end_stage_inferred": inferred_end_stage,
            "partial": cur["partial"] or outcome != "cleared",
        }
        self.records.append(record)
        self.emit(record)

    def observe(self, stage, now, wall, source="poll"):
        stage = stage_number(stage)
        if stage is None:
            return
        if self.current is None:
            self._begin(stage, now, wall, source, partial=source == "poll")
            return
        cur = self.current
        if stage < cur["stage"]:
            return  # 忽略迟到的旧关卡响应，避免计时倒退。
        if stage == cur["stage"]:
            # nextStage 可能先打开选技能面板；stageStart 才确认新关正式开始。
            if source == "game/stageStart" and cur["source"] != source and cur["end"] is None:
                self._begin(stage, now, wall, source)
            return
        outcome = "cleared" if stage == cur["stage"] + 1 else "gap"
        self._record(now, wall, outcome, source, next_stage=stage)
        self._begin(stage, now, wall, source)

    def on_api(self, api, payload, now, wall):
        if not api_ok(payload):
            return
        stage = api_stage(payload)
        if api in ("game/stageStart", "game/nextStage"):
            self.observe(stage, now, wall, api)
        elif api == "game/stageEnd" and self.current is not None:
            if stage is None or stage == self.current["stage"]:
                # 无关卡号时只暂记候选；不在这里宣布通关，也不重置重复响应。
                if self.current["end"] is None:
                    self.current["end"] = (now, wall, api, stage is None)

    def finish(self, now, wall, reason):
        if self.current is not None:
            self._record(now, wall, "unfinished", reason)
            self.current = None
