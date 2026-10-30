"""对战期间的请求结构监控；只观察，不修改或拦截游戏请求。"""
from collections import deque
import datetime
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlsplit

from diagnostics import safe_api_request_data


BASELINE_NAME = 'api_baseline.json'
MAX_BYTES = 256 * 1024


def baseline_path(directory):
    """单文件 EXE 首次启动时导出基准；已有外部文件绝不覆盖。"""
    target = Path(directory) / BASELINE_NAME
    if not target.exists():
        source = Path(__file__).resolve().with_name(BASELINE_NAME)
        content = source.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('xb') as stream:
                stream.write(content)
        except FileExistsError:
            pass
    return target


def payload_fields(raw, query=False):
    """完整检查字段（包括表单内嵌 JSON），超预算则报警，绝不静默截断。"""
    raw = raw or ''
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise ValueError('请求数据超过 256 KiB 检查上限')
    if not raw:
        return set()
    if query or not raw.lstrip().startswith(('{', '[')):
        if not query and '=' not in raw:
            raise ValueError('无法识别的请求体格式')
        parsed = parse_qs(raw, keep_blank_values=True, max_num_fields=4096)
        data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
    else:
        data = json.loads(raw)
    fields = set()

    def visit(value, path, depth):
        if depth > 20 or len(fields) > 4096:
            raise ValueError('请求字段超过深度或数量检查上限')
        if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
            try:
                value = json.loads(value)
            except ValueError:
                raise ValueError('内嵌 JSON 无法解析') from None
        if isinstance(value, dict):
            for key, item in value.items():
                part = str(key).replace('~', '~0').replace('/', '~1')
                child = path + '/' + part
                fields.add(child)
                visit(item, child, depth + 1)
        elif isinstance(value, list):
            fields.add(path + '/[]')
            for item in value:
                visit(item, path + '/[]', depth + 1)
    visit(data, '', 0)
    return fields


def matches_field(actual, expected):
    parts, pattern = actual.split('/'), expected.split('/')
    return len(parts) == len(pattern) and all(
        a == b or (b == '{card_id}' and re.fullmatch(r'[0-9]+', a))
        for a, b in zip(parts, pattern))


class ApiMonitor:
    def __init__(self, path):
        self.path = Path(path)
        self.active = False
        self.phase = ''
        self.since_ms = 0
        self.pending = None
        self.recent = deque(maxlen=16)
        self.reload()

    def reload(self):
        self.active = False
        self.pending = None
        self.error = None
        self.recent.clear()
        self.template = {}
        self.digest = None
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_BYTES:
                raise ValueError('基准文件过大')
            obj = json.loads(raw)
            if not isinstance(obj, dict) or obj.get('schema_version') != 1:
                raise ValueError('不支持的基准格式版本')
            requests = obj.get('requests')
            if not isinstance(requests, dict) or not requests:
                raise ValueError('缺少接口基准')
            if not isinstance(obj.get('hosts'), list) or not obj['hosts']:
                raise ValueError('缺少接口域名')
            for endpoint, rule in requests.items():
                if not re.fullmatch(r'(GET|POST) /japi/[^?\s]+/actgame/[^?\s]+', endpoint):
                    raise ValueError('接口定义格式无效')
                for side in ('query', 'body'):
                    fields = rule[side]
                    if not isinstance(fields, list) or not all(
                            isinstance(p, str) and p.startswith('/') for p in fields):
                        raise ValueError('接口字段列表无效')
            self.template = obj
            self.digest = hashlib.sha256(raw).hexdigest()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error = '无法读取 API 基准：' + type(exc).__name__
            self.pending = {'kind': 'baseline_error', 'message': self.error}

    def arm(self, phase):
        if self.error or self.pending:
            return
        if not self.active:
            self.since_ms = time.time() * 1000
            self.recent.clear()
        self.active, self.phase = True, phase

    def suspend(self):
        self.active = False

    def observe(self, url, method, body='', started_ms=None):
        if not self.active or self.pending:
            return None
        # Playwright 暂停期间积压的事件在恢复后才分派，按真实发出时间过滤。
        if isinstance(started_ms, (int, float)) and started_ms > 0 and started_ms < self.since_ms:
            return None
        parsed = urlsplit(url)
        if '/actgame/' not in parsed.path:
            return None
        endpoint = method.upper() + ' ' + parsed.path
        rule = self.template['requests'].get(endpoint)
        diffs, actual = [], {}
        if parsed.hostname not in self.template['hosts']:
            diffs.append({'kind': 'new_host', 'host': parsed.hostname})
        if rule is None:
            diffs.append({'kind': 'new_endpoint', 'endpoint': endpoint})
        for side, raw in (('query', parsed.query), ('body', body or '')):
            try:
                fields = payload_fields(raw, query=side == 'query')
                actual[side] = sorted(fields)
                if rule is not None:
                    extra = sorted(f for f in fields if not any(matches_field(f, p) for p in rule[side]))
                    if extra:
                        diffs.append({'kind': 'new_fields', 'location': side, 'fields': extra})
            except (ValueError, TypeError, RecursionError) as exc:
                diffs.append({'kind': 'unreadable_payload', 'location': side,
                              'error_type': type(exc).__name__})
        item = {'at': datetime.datetime.now().isoformat(timespec='milliseconds'),
                'endpoint': endpoint, 'phase': self.phase, 'fields': actual}
        if not diffs:
            self.recent.append(item)
            return None
        self.pending = {**item, 'kind': 'api_change', 'differences': diffs,
                        'expected': rule, 'recent_requests': list(self.recent),
                        'request': {'host': parsed.hostname,
                                    'query': safe_api_request_data(parsed.query) if len(parsed.query) <= MAX_BYTES else '[oversized]',
                                    'body': safe_api_request_data(body) if len(body or '') <= MAX_BYTES else '[oversized]'}}
        self.suspend()
        return self.pending

    def write_report(self, directory, context, restored):
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = root / ('api_change_' + stamp + '.json')
        report = {'schema_version': 1, 'baseline_file': self.path.name,
                  'baseline_sha256': self.digest, 'baseline_id': self.template.get('baseline_id'),
                  'context': context, 'runtime_restored': restored,
                  'action': '暂停助手并停止 API 检查，保留浏览器与场次；不拦截或撤回原生请求',
                  'incident': self.pending}
        with path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        return str(path)
