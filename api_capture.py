"""全域被动网络采集；不路由、不重放请求、不执行页面脚本。"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import zipfile


_SECRET_NAMES = {
    'cookie', 'setcookie', 'authorization', 'proxyauthorization', 'password',
    'passwd', 'secret', 'signature', 'sign', 'salt', 'uid', 'userid', 'did',
    'deviceid', 'session', 'sessionid', 'auth', 'acf', 'nickname', 'nn',
    'phone', 'mobilephone', 'email', 'username',
}
_TEXT_CONTENT = re.compile(r'text/|json|javascript|xml|graphql|x-www-form-urlencoded', re.I)
_STATIC = {'image', 'media', 'font', 'stylesheet'}
_TEXT_SECRET = re.compile(
    r'''(?i)(["']?(?:[a-z0-9_-]*(?:token|password|secret|authorization)|signature|sign|cookie|sessionid)["']?\s*[:=]\s*)(["'])(.*?)(\2)''')
_BARE_SECRET = re.compile(
    r'''(?i)(\b(?:[a-z0-9_-]*(?:token|password|secret|authorization)|signature|sign|cookie|sessionid)\s*=\s*)([^&\s;<>"']+)''')


def _secret(key):
    normalized = re.sub('[^a-z0-9]', '', str(key).lower())
    return normalized in _SECRET_NAMES or any(
        part in normalized for part in ('token', 'password', 'secret', 'csrf', 'xsrf'))


def _redacted(value):
    return {'redacted': True, 'length': len(str(value))}


def _clean(value, key='', depth=0):
    if _secret(key):
        return _redacted(value)
    if depth > 24:
        return {'omitted': 'depth-limit'}
    if isinstance(value, dict):
        return {str(k): _clean(v, k, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, depth=depth + 1) for v in value]
    if isinstance(value, str):
        # 表单里的 ctn/stat/data 等经常又是一层 JSON，保留其全部业务结构。
        if value.lstrip().startswith(('{', '[')):
            try:
                return {'encoding': 'json-string', 'data': _clean(json.loads(value), depth=depth + 1)}
            except (ValueError, RecursionError):
                pass
        if value.startswith(('http://', 'https://', 'ws://', 'wss://')):
            return sanitize_url(value, depth + 1)
        text = _TEXT_SECRET.sub(lambda m: m[1] + m[2] + '[redacted]' + m[2], value)
        return _BARE_SECRET.sub(lambda m: m[1] + '[redacted]', text)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return '[unsupported]'


def sanitize_url(url, depth=0):
    try:
        parsed = urlsplit(str(url))
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            clean = _clean(value, key, depth + 1)
            query.append((key, json.dumps(clean, ensure_ascii=False) if isinstance(clean, (dict, list)) else clean))
        # 不保留 URL user:password 和 fragment。
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path,
                           urlencode(query), ''))
    except (ValueError, RecursionError):
        return '[invalid-url]'


def payload_view(value, content_type='', limit=16 * 1024 * 1024):
    if value is None:
        return {'format': 'empty', 'length': 0}
    raw = value if isinstance(value, bytes) else str(value).encode('utf-8', 'replace')
    result = {'length': len(raw)}
    if limit and len(raw) > limit:
        return dict(result, format='omitted', reason='body-limit', sha256=hashlib.sha256(raw).hexdigest())
    if not raw:
        return dict(result, format='empty')
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return dict(result, format='binary', base64=base64.b64encode(raw).decode('ascii'))
    try:
        return dict(result, format='json', data=_clean(json.loads(text)))
    except (ValueError, RecursionError):
        pass
    if 'x-www-form-urlencoded' in content_type.lower():
        fields = {}
        for key, value in parse_qsl(text, keep_blank_values=True):
            if key in fields:
                old = fields[key]
                fields[key] = old + [value] if isinstance(old, list) else [old, value]
            else:
                fields[key] = value
        return dict(result, format='form', data=_clean(fields))
    if isinstance(value, str) or _TEXT_CONTENT.search(content_type) or raw[:1] in (b'<', b'{', b'['):
        return dict(result, format='text', text=_clean(text))
    return dict(result, format='binary', base64=base64.b64encode(raw).decode('ascii'))


def headers_view(headers):
    # list 形式保留同名的多条 Set-Cookie/Header，不读取浏览器 Cookie 数据库。
    pairs = headers.items() if isinstance(headers, dict) else ((h['name'], h['value']) for h in headers)
    result = []
    for name, value in pairs:
        lower = name.lower()
        if _secret(name):
            safe = _redacted(value)
        elif lower in ('referer', 'referrer', 'location', 'content-location'):
            safe = sanitize_url(value)
        else:
            safe = _clean(value)
        result.append({'name': name, 'value': safe})
    return result


class ApiCapture:
    def __init__(self, directory, on_log=None, body_limit=16 * 1024 * 1024):
        stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.directory = Path(directory) / ('api_lab_' + stamp)
        self.directory.mkdir(parents=True)
        self.path = str(self.directory / 'network.jsonl')
        self.summary_path = str(self.directory / 'summary.json')
        self.zip_path = str(self.directory.with_suffix('.zip'))
        self._file = open(self.path, 'x', encoding='utf-8')
        self._lock = threading.RLock()
        self._seq = self._next_request = self._next_socket = 0
        # 保存请求对象直到会话结束，避免对象 id 重用或提前回收打乱请求/回复关联。
        self._requests = {}
        self._pages = set()
        self._sessions = []
        self._on_log = on_log
        self._context = None
        self.body_limit = max(0, int(body_limit))
        self.counts = {}
        self.endpoints = {}
        self.started = time.time()
        self._closed = False
        self.phase = '大厅'
        self._write('session', scope='all-context-http-and-page-websocket-sse',
                    body_limit_bytes=self.body_limit, credentials='sensitive-fields-redacted',
                    static_bodies='image/media/font/stylesheet: metadata only unless API/report',
                    opaque_payloads='binary/base64 and unrecognized strings may still contain private data',
                    behavior='passive; no route, replay, click, game injection or browser fingerprint override')

    def _write(self, kind, **data):
        with self._lock:
            if self._closed:
                return
            self._seq += 1
            self.counts[kind] = self.counts.get(kind, 0) + 1
            record = {'seq': self._seq, 'at': dt.datetime.now().isoformat(timespec='milliseconds'),
                      'elapsed_s': round(time.time() - self.started, 3),
                      'kind': kind, 'phase': self.phase, **data}
            self._file.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
            self._file.flush()
        if self._on_log:
            self._on_log(kind, record)

    def mark(self, label):
        with self._lock:
            self.phase = str(label)[:160]
            self._write('marker', label=self.phase)

    def _info(self, request):
        if request not in self._requests:
            self._next_request += 1
            self._requests[request] = {'id': 'r%06d' % self._next_request, 'phase': self.phase,
                                       'response': False, 'finished': False}
        return self._requests[request]

    @staticmethod
    def _relevant(url, resource_type=''):
        parsed = urlsplit(url)
        return resource_type not in _STATIC or bool(re.search(
            r'/actgame/|/fish3/|/api/|/japi/|report|beacon|telemetry|track', parsed.path, re.I))

    def _error(self, phase, exc, **context):
        self._write('capture_error', source=phase, error=type(exc).__name__, **context)

    def install(self, context):
        self._context = context
        # metadata 先写，正文只在完成后读取；避免 SSE/长连接阻塞整个监听线程。
        context.on('request', self.on_request)
        context.on('response', self.on_response)
        context.on('requestfinished', self.on_request_finished)
        context.on('requestfailed', self.on_request_failed)
        context.on('page', self._attach_page)
        for page in context.pages:
            self._attach_page(page)

    def _attach_page(self, page):
        page_key = id(page)
        if page_key in self._pages:
            return
        self._pages.add(page_key)
        try:
            session = self._context.new_cdp_session(page)
            self._sessions.append(session)
            scope = 'page%d:' % len(self._sessions)
            session.on('Network.eventSourceMessageReceived', lambda ev: self._write(
                'eventsource_message', request_id=scope + str(ev.get('requestId')),
                event=ev.get('eventName'), event_id=ev.get('eventId'),
                payload=payload_view(ev.get('data', ''), 'text/plain', self.body_limit)))
            session.on('Network.requestWillBeSent', lambda ev: self._cdp_sse_request(ev, scope))
            session.on('Network.webSocketWillSendHandshakeRequest', lambda ev: self._write(
                'websocket_handshake_request', websocket_id=scope + str(ev.get('requestId')),
                headers=headers_view((ev.get('request') or {}).get('headers', {}))))
            session.on('Network.webSocketHandshakeResponseReceived', lambda ev: self._write(
                'websocket_handshake_response', websocket_id=scope + str(ev.get('requestId')),
                status=(ev.get('response') or {}).get('status'),
                headers=headers_view((ev.get('response') or {}).get('headers', {}))))
            session.on('Network.webSocketCreated', lambda ev: self._write(
                'websocket_open', websocket_id=scope + str(ev.get('requestId')),
                url=sanitize_url(ev.get('url', ''))))
            session.on('Network.webSocketFrameSent', lambda ev: self._cdp_ws_frame(ev, scope, 'sent'))
            session.on('Network.webSocketFrameReceived', lambda ev: self._cdp_ws_frame(ev, scope, 'received'))
            session.on('Network.webSocketClosed', lambda ev: self._write(
                'websocket_close', websocket_id=scope + str(ev.get('requestId'))))
            session.on('Network.webSocketFrameError', lambda ev: self._write(
                'websocket_error', websocket_id=scope + str(ev.get('requestId')),
                error=str(ev.get('errorMessage', 'unknown'))))
            session.send('Network.enable')
        except Exception as exc:
            self._error('sse-handshake-listener', exc)
            page.on('websocket', self.on_websocket)

    def _cdp_sse_request(self, event, scope):
        if event.get('type') == 'EventSource':
            req = event.get('request') or {}
            self._write('eventsource_connection', request_id=scope + str(event.get('requestId')),
                        method=req.get('method'), url=sanitize_url(req.get('url', '')))

    def _cdp_ws_frame(self, event, scope, direction):
        frame = event.get('response') or {}
        value = frame.get('payloadData', '')
        opcode = frame.get('opcode')
        if opcode != 1:
            try:
                value = base64.b64decode(value, validate=True)
            except ValueError:
                self._error('websocket-base64', ValueError(), websocket_id=scope + str(event.get('requestId')))
                return
        self._write('websocket_frame', websocket_id=scope + str(event.get('requestId')),
                    direction=direction, opcode=opcode, masked=frame.get('mask'),
                    payload=payload_view(value, 'text/plain' if opcode == 1 else '', self.body_limit))

    def _headers(self, obj, request_id, source):
        try:
            return headers_view(obj.headers_array())
        except Exception as exc:
            self._error(source + '-full-headers', exc, request_id=request_id)
            return headers_view(obj.headers)

    def on_request(self, request):
        try:
            info = self._info(request)
            url, method, resource_type = request.url, request.method, request.resource_type
            try:
                frame_url = sanitize_url(request.frame.url)
            except Exception:
                frame_url = None  # service worker 的请求没有 frame。
            try:
                body = payload_view(request.post_data_buffer,
                                    request.headers.get('content-type', ''), self.body_limit)
            except Exception as exc:
                body = {'format': 'unavailable', 'reason': type(exc).__name__}
            redirected = request.redirected_from
            parsed = urlsplit(url)
            endpoint = method + ' ' + parsed.netloc.rsplit('@', 1)[-1] + parsed.path
            self.endpoints[endpoint] = self.endpoints.get(endpoint, 0) + 1
            self._write('request', request_id=info['id'], phase=info['phase'], method=method,
                        resource_type=resource_type, url=sanitize_url(url), page_url=frame_url,
                        redirected_from=self._info(redirected)['id'] if redirected else None,
                        headers=self._headers(request, info['id'], 'request'), body=body)
        except Exception as exc:
            self._error('request', exc)

    def on_response(self, response):
        try:
            info = self._info(response.request)
            if info['response']:
                return
            info['response'] = True
            self._write('response', request_id=info['id'], phase=info['phase'], status=response.status,
                        status_text=response.status_text, url=sanitize_url(response.url),
                        resource_type=response.request.resource_type,
                        from_service_worker=bool(getattr(response, 'from_service_worker', False)),
                        headers=self._headers(response, info['id'], 'response'))
        except Exception as exc:
            self._error('response', exc)

    def on_request_finished(self, request):
        try:
            info = self._info(request)
            if info['finished']:
                return
            info['finished'] = True
            response = request.response()
            if response is None:
                self._write('response_body', request_id=info['id'], phase=info['phase'],
                            body={'format': 'unavailable', 'reason': 'no-response'})
                return
            self.on_response(response)
            headers = response.headers
            ct = headers.get('content-type', '')
            length = headers.get('content-length')
            relevant = self._relevant(response.url, request.resource_type)
            if not relevant:
                body = {'format': 'omitted', 'reason': 'static-resource', 'content_length': length}
            elif 'text/event-stream' in ct:
                body = {'format': 'event-stream', 'reason': 'messages-recorded-as-events'}
            elif self.body_limit and length and length.isdigit() and int(length) > self.body_limit:
                body = {'format': 'omitted', 'reason': 'body-limit', 'length': int(length)}
            elif request.method == 'HEAD' or response.status in (204, 304) or 300 <= response.status < 400:
                body = {'format': 'empty', 'length': 0}
            else:
                try:
                    body = payload_view(response.body(), ct, self.body_limit)
                except Exception as exc:
                    body = {'format': 'unavailable', 'reason': type(exc).__name__}
            self._write('response_body', request_id=info['id'], phase=info['phase'],
                        url=sanitize_url(response.url), body=body, timing=request.timing)
        except Exception as exc:
            self._error('response-body', exc)

    def on_request_failed(self, request):
        info = self._info(request)
        info['finished'] = True
        self._write('request_failed', request_id=info['id'], phase=info['phase'],
                    url=sanitize_url(request.url), method=request.method,
                    resource_type=request.resource_type, failure=str(request.failure or 'unknown'))

    def on_websocket(self, websocket):
        self._next_socket += 1
        ws_id = 'ws%06d' % self._next_socket
        self._write('websocket_open', websocket_id=ws_id, url=sanitize_url(websocket.url))
        websocket.on('framesent', lambda value: self._ws_frame(ws_id, 'sent', value))
        websocket.on('framereceived', lambda value: self._ws_frame(ws_id, 'received', value))
        websocket.on('socketerror', lambda error: self._write(
            'websocket_error', websocket_id=ws_id, error=str(error)))
        websocket.on('close', lambda: self._write('websocket_close', websocket_id=ws_id))

    def _ws_frame(self, ws_id, direction, value):
        self._write('websocket_frame', websocket_id=ws_id, direction=direction,
                    payload=payload_view(value, 'text/plain' if isinstance(value, str) else '', self.body_limit))

    def close(self):
        with self._lock:
            if self._closed:
                return
            unfinished = [v['id'] for v in self._requests.values() if not v['finished']]
            self._write('capture_end', unfinished_requests=unfinished)
            summary = {'started': dt.datetime.fromtimestamp(self.started).isoformat(),
                       'ended': dt.datetime.now().isoformat(), 'counts': dict(self.counts),
                       'endpoints': self.endpoints, 'unfinished_requests': unfinished,
                       'body_limit_bytes': self.body_limit, 'capture_file': 'network.jsonl'}
            self._closed = True
            self._file.close()
            Path(self.summary_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            with zipfile.ZipFile(self.zip_path, 'x', zipfile.ZIP_DEFLATED) as bundle:
                for path in (self.path, self.summary_path):
                    bundle.write(path, arcname=Path(path).name)
            self._requests.clear()
        if self._on_log:
            self._on_log('summary', dict(summary, zip_path=self.zip_path))
