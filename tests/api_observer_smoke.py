"""本地模拟完整监听会话；需要 CHROME_PATH 与 Patchright。"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import api_lab_browser


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def reply(self, body, kind='application/json', status=200, headers=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Set-Cookie', 'acf_auth=SUPER_SECRET; Path=/')
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.reply(json.dumps({'error': 0, 'cards': [{'cardId': 7, 'level': 8}],
                               'token': 'SUPER_SECRET', 'coin': 90}))

    def do_GET(self):
        if self.path.startswith('/socket'):
            key = self.headers['Sec-WebSocket-Key']
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            self.wfile.write(b'\x81\x0b{"hello":1}')
            self.wfile.flush()
            self.connection.settimeout(4)
            try:
                head = self.rfile.read(2)
                if len(head) == 2:
                    length = head[1] & 127
                    mask = self.rfile.read(4)
                    data = self.rfile.read(length)
                    if mask:
                        data = bytes(v ^ mask[i % 4] for i, v in enumerate(data))
                    packet = json.dumps({'reply': json.loads(data)}).encode()
                    self.wfile.write(bytes([0x81, len(packet)]) + packet + b'\x88\x02\x03\xe8')
                    self.wfile.flush()
            except (OSError, ValueError):
                pass
            self.close_connection = True
        elif self.path.startswith('/sse'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try:
                for _ in range(15):
                    self.wfile.write(b'data: {"cardId":7,"effect":2}\n\n')
                    self.wfile.flush()
                    time.sleep(.2)
            except OSError:
                pass
            self.close_connection = True
        elif self.path == '/':
            html = '''<html><body>Passive observer fixture<iframe src="/frame"></iframe><script>
              new EventSource('/sse');
              const ws = new WebSocket('ws://' + location.host + '/socket');
              ws.onopen = () => ws.send(JSON.stringify({formation:[1,2,3]}));
              setTimeout(async () => {
                await fetch('/packs/open',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
                  body:'cardId=7&authToken=SUPER_SECRET'});
                await fetch('/cards/upgradeAll',{method:'POST',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({cardId:7,level:8,ctn:JSON.stringify({token:'SUPER_SECRET',count:3})})});
                await fetch('http://localhost:PORT/fish3/1.gif?payload='+encodeURIComponent(JSON.stringify({
                  webglRenderer:'fixture-GPU',deviceMemory:8,token:'SUPER_SECRET'})));
                await fetch('/redirect');
                window.open('/popup');
              },350);
              setTimeout(()=>fetch('/late-call'),1400);
            </script></body></html>'''.replace('PORT', str(self.server.server_port))
            self.reply(html, 'text/html')
        elif self.path == '/frame':
            self.reply('<script>fetch("/formation/list")</script>', 'text/html')
        elif self.path == '/popup':
            self.reply('<script>fetch("/formation/update")</script>', 'text/html')
        elif self.path == '/redirect':
            self.reply(b'', status=302, headers=[('Location', '/result?token=SUPER_SECRET')])
        elif self.path.startswith('/fish3'):
            self.reply(b'GIF89a\x00\xff', 'image/gif')
        else:
            self.reply('{"error":0,"data":{"success":true}}')


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    rows = []
    ready = threading.Event()
    def record(kind, row):
        rows.append((kind, row))
        finished = {r.get('url', '').split('?')[0].split('/')[-1]
                    for k, r in rows if k == 'response_body'}
        if {'open', 'upgradeAll', '1.gif', 'late-call', 'update'} <= finished and \
                any(k == 'eventsource_message' for k, _ in rows) and \
                sum(k == 'websocket_frame' for k, _ in rows) >= 3:
            ready.set()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            session = api_lab_browser.ObserverSession(profile=str(Path(tmp) / 'profile'),
                                                       logdir=tmp, on_event=record)
            def launch(pw):
                return pw.chromium.launch_persistent_context(
                    str(Path(tmp) / 'profile'), executable_path=os.environ['CHROME_PATH'],
                    headless=True, args=['--no-sandbox', '--disable-popup-blocking']), os.environ['CHROME_PATH']
            with patch.object(session, '_launch', side_effect=launch), \
                    patch.object(api_lab_browser, 'GAME_URL', f'http://127.0.0.1:{server.server_port}/'):
                worker = threading.Thread(target=session.run, daemon=True)
                worker.start()
                session.mark('开包及强化')
                ok = ready.wait(15)
                session.stop()
                worker.join(15)
                assert not worker.is_alive(), 'worker did not exit'
                assert ok, [(k, r) for k, r in rows if k in ('fatal', 'capture_error', 'response_body', 'websocket_frame')]
            assert session.archive
            with zipfile.ZipFile(session.archive) as bundle:
                raw = bundle.read('network.jsonl').decode()
                packets = [json.loads(line) for line in raw.splitlines()]
            assert not any(r['kind'] == 'capture_error' for r in packets), [r for r in packets if r['kind'] == 'capture_error']
            # 样例 JS、嵌套 JSON、Cookie、表单与跨域统计参数中的凭据均不以原文保存。
            assert 'SUPER_SECRET' not in raw
            assert 'fixture-GPU' in raw
            assert any(r['kind'] == 'websocket_handshake_response' and r['status'] == 101 for r in packets)
            requests = {r['request_id']: r for r in packets if r['kind'] == 'request'}
            bodies = [r for r in packets if r['kind'] == 'response_body' and '/cards/upgradeAll' in r['url']]
            assert len(bodies) == 1 and bodies[0]['body']['data']['cards'][0]['level'] == 8
            assert requests[bodies[0]['request_id']]['phase'] == '开包及强化'
            print(json.dumps({'requests': len(requests), 'responses': sum(r['kind'] == 'response' for r in packets),
                'websocket_frames': sum(r['kind'] == 'websocket_frame' for r in packets),
                'sse_messages': sum(r['kind'] == 'eventsource_message' for r in packets),
                'passive_session': 'OK', 'archive': 'OK'}, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
