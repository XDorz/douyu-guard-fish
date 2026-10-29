"""GUI 配置文件：独立于 EXE，校验输入并原子写入。"""
import json
import math
import os
import tempfile


DEFAULTS = {
    "rid": "12306", "rounds": "0", "gap_min": "5", "gap_max": "15",
    "hp_guard": True, "hp_threshold": "5", "coin_play": True,
    "one_hit": False, "one_hit_mode": "full", "one_hit_probability": "50",
    "attack_multiplier": "2", "headless": False,
    "stop_stage": "0", "advance_stages": "0", "max_minutes": "13",
}
LIMITS = {
    "rounds": (0, None, True), "gap_min": (0, None, False),
    "gap_max": (0, None, False), "hp_threshold": (1, None, True),
    "stop_stage": (0, None, True), "advance_stages": (0, None, True),
    "max_minutes": (0.001, None, False),
    "one_hit_probability": (0, 100, False), "attack_multiplier": (0.01, None, False),
}


def normalize(values, base=None):
    result = dict(DEFAULTS if base is None else base)
    if not isinstance(values, dict):
        return result
    for key, default in DEFAULTS.items():
        value = values.get(key)
        if isinstance(default, bool):
            if isinstance(value, bool):
                result[key] = value
        elif key in LIMITS:
            try:
                if isinstance(value, bool):
                    continue
                num = float(value)
                lo, hi, integral = LIMITS[key]
                if not math.isfinite(num) or num < lo or (hi is not None and num > hi):
                    continue
                if integral and not num.is_integer():
                    continue
                result[key] = str(int(num)) if integral else format(num, ".12g")
            except (TypeError, ValueError, OverflowError):
                pass
        elif key == "one_hit_mode":
            if value in ("full", "probability", "multiplier"):
                result[key] = value
        elif key == "rid" and isinstance(value, str) and value.strip():
            result[key] = value.strip()[:64]
    return result


def load(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("配置根节点应为对象")
        return normalize(data.get("gui", data)), None
    except FileNotFoundError:
        return dict(DEFAULTS), None
    except (OSError, ValueError) as exc:
        return dict(DEFAULTS), str(exc)


def save(path, values):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".config-", suffix=".tmp", delete=False) as f:
            tmp_path = f.name
            json.dump({"schema_version": 1, "gui": normalize(values)}, f,
                      ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
