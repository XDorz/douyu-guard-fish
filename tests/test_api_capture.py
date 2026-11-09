import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from api_capture import ApiCapture, payload_view, sanitize_url, headers_view


class ApiCaptureTests(unittest.TestCase):
    def test_sensitive_camelcase_nested_json_and_headers_keep_game_fields(self):
        view = payload_view(json.dumps({
            'cardId': 42, 'formation': [1, 2, 3], 'deviceMemory': 8,
            'userInfo': {'uid': 99, 'coin': 10},
            'battleToken': 'secret-battle', 'sign': 'secret-sign',
            'ctn': json.dumps({'accessToken': 'secret-nested', 'level': 9}),
        }), 'application/json')
        self.assertEqual(view['data']['cardId'], 42)
        self.assertEqual(view['data']['deviceMemory'], 8)
        self.assertEqual(view['data']['userInfo']['coin'], 10)
        self.assertEqual(view['data']['ctn']['data']['level'], 9)
        self.assertNotIn('secret-', json.dumps(view))
        url = sanitize_url('https://u:pwd@example.test/cards?token=secret-url&cardId=42&ctn=' +
                           '%7B%22accessToken%22%3A%22secret-embedded%22%7D')
        self.assertIn('cardId=42', url)
        self.assertNotIn('secret-', url)
        self.assertNotIn('pwd', url)
        headers = headers_view([{'name': 'Cookie', 'value': 'secret-cookie'},
                                {'name': 'Set-Cookie', 'value': 'secret-a'},
                                {'name': 'Set-Cookie', 'value': 'secret-b'},
                                {'name': 'User-Agent', 'value': 'real-UA'},
                                {'name': 'Location', 'value': 'https://example.test/?token=secret-location'}])
        self.assertEqual(len(headers), 5)
        self.assertNotIn('secret-', json.dumps(headers))
        self.assertEqual(headers[3]['value'], 'real-UA')
        html = payload_view("<script>fetch('/x', {body:'cardId=7&authToken=secret-form'});</script>", 'text/html')
        self.assertNotIn('secret-form', json.dumps(html))

    def test_preserve_large_card_lists_binary_and_report_omissions(self):
        cards = [{'cardId': i, 'level': 1} for i in range(1200)]
        self.assertEqual(len(payload_view(json.dumps(cards), 'application/json')['data']), 1200)
        body = b'\x00\xff' * 5000
        view = payload_view(body)
        import base64
        self.assertEqual(base64.b64decode(view['base64']), body)
        self.assertEqual(payload_view(body, limit=100)['reason'], 'body-limit')
        self.assertEqual(payload_view(body, limit=0)['format'], 'binary')
        form = payload_view('card=7&card=8&authToken=secret', 'application/x-www-form-urlencoded')
        self.assertEqual(form['data']['card'], ['7', '8'])
        self.assertNotIn('secret', json.dumps(form))

    def request(self, url='https://example.test/card/open', resource='xhr'):
        req = Mock()
        req.url, req.method, req.resource_type = url, 'POST', resource
        req.headers = {'content-type': 'application/json'}
        req.headers_array.return_value = [{'name': 'Content-Type', 'value': 'application/json'},
                                          {'name': 'Cookie', 'value': 'private-cookie'}]
        req.post_data_buffer = b'{"cardId":7,"token":"private-token"}'
        req.frame.url = 'https://example.test/game'
        req.redirected_from = None
        req.timing = {'startTime': 1000, 'responseEnd': 45}
        req.failure = 'net::ERR_FAILED'
        return req

    def response(self, req):
        res = Mock()
        res.request = req
        res.url, res.status, res.status_text = req.url, 200, 'OK'
        res.from_service_worker = False
        res.headers = {'content-type': 'application/json'}
        res.headers_array.return_value = [{'name': 'Content-Type', 'value': 'application/json'},
                                          {'name': 'Set-Cookie', 'value': 'private-cookie'}]
        res.body.return_value = b'{"error":0,"data":{"level":4}}'
        req.response.return_value = res
        return res

    def test_headers_are_logged_without_waiting_for_body_and_requests_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = ApiCapture(tmp)
            req = self.request()
            res = self.response(req)
            cap.on_request(req)
            cap.on_response(res)
            res.body.assert_not_called()
            # 完成事件可以重复；正文只读取一次。
            cap.on_request_finished(req)
            cap.on_request_finished(req)
            res.body.assert_called_once()
            cap.mark('一键强化')
            failed = self.request('https://other.test/unknown')
            cap.on_request(failed)
            cap.on_request_failed(failed)
            cap.close()
            rows = [json.loads(x) for x in Path(cap.path).read_text().splitlines()]
            request = next(r for r in rows if r['kind'] == 'request')
            body = next(r for r in rows if r['kind'] == 'response_body')
            self.assertEqual(request['request_id'], body['request_id'])
            self.assertEqual(body['body']['data']['data']['level'], 4)
            self.assertNotIn('private-', Path(cap.path).read_text())
            self.assertEqual(next(r for r in rows if r['kind'] == 'request_failed')['phase'], '一键强化')
            self.assertTrue(all(r['kind'] != 'capture_error' for r in rows))
            with zipfile.ZipFile(cap.zip_path) as bundle:
                self.assertEqual(set(bundle.namelist()), {'network.jsonl', 'summary.json'})
                summary = json.loads(bundle.read('summary.json'))
            self.assertEqual(summary['counts']['request'], 2)
            self.assertFalse(summary['unfinished_requests'])

    def test_metadata_fallback_and_sse_do_not_block_reading_stream_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = ApiCapture(tmp)
            req = self.request(resource='eventsource')
            res = self.response(req)
            res.headers = {'content-type': 'text/event-stream'}
            res.headers_array.side_effect = RuntimeError('missing extra headers')
            cap.on_response(res)
            cap.on_request_finished(req)
            res.body.assert_not_called()
            cap.close()
            text = Path(cap.path).read_text()
            self.assertIn('full-headers', text)
            self.assertIn('event-stream', text)

    def test_ws_frames_and_unfinished_requests_have_explicit_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = ApiCapture(tmp)
            ws = Mock()
            ws.url = 'wss://example.test/socket?token=secret'
            cap.on_websocket(ws)
            callbacks = {c.args[0]: c.args[1] for c in ws.on.call_args_list}
            callbacks['framesent']('{"formation":[1,2]}')
            callbacks['framereceived'](b'\xff\x00')
            callbacks['close']()
            cap.on_request(self.request())
            cap.close()
            records = [json.loads(x) for x in Path(cap.path).read_text().splitlines()]
            self.assertEqual(sum(r['kind'] == 'websocket_frame' for r in records), 2)
            self.assertEqual(len(records[-1]['unfinished_requests']), 1)

    def test_browser_session_has_no_game_actions_or_request_routes(self):
        import ast
        path = Path(__file__).parents[1] / 'api_lab_browser.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        for forbidden in ('evaluate', 'add_init_script', 'route', 'click', 'fill', 'reload'):
            self.assertNotIn(forbidden, calls)
        names = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertNotIn('GuardFishBot', names)
        self.assertNotIn('kill_stale_chrome', names)


if __name__ == '__main__':
    unittest.main()
