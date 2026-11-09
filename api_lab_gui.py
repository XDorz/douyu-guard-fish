"""API 监听实验版，独立入口，不启动挂机或游戏修改代码。"""
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import urlsplit

from api_lab_browser import ObserverSession, DATA_DIR, LOGDIR, DEFAULT_RID
from settings import load as load_settings


LAB_VERSION = Path(__file__).with_name('API_LAB_VERSION').read_text(encoding='utf-8').strip()


class ApiLabApp:
    def __init__(self, root):
        self.root = root
        self.root.title('保卫鱼娘 · 只读网络监听 v' + LAB_VERSION)
        self.root.geometry('1060x710')
        self.root.minsize(850, 560)
        self.events = queue.Queue()
        self.session = self.thread = None
        self.archive = None
        self._closing = False
        self._line_count = 0
        self._counts = {'request': 0, 'response': 0, 'websocket_frame': 0, 'capture_error': 0}
        config, _ = load_settings(os.path.join(DATA_DIR, 'config.json'))
        self.rid = tk.StringVar(value=config.get('rid', DEFAULT_RID))
        self.limit = tk.StringVar(value='16')
        self.status = tk.StringVar(value='未启动；与原助手共用登录目录，请先关闭原助手。')
        self.output = tk.StringVar(value='')
        self.phase = tk.StringVar(value='大厅')
        self._build()
        self._pump_job = root.after(100, self._pump)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def _build(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill='x')
        ttk.Label(top, text='房间号').pack(side='left')
        ttk.Entry(top, textvariable=self.rid, width=9).pack(side='left', padx=(5, 12))
        self.start_btn = ttk.Button(top, text='打开游戏并监听', command=self.start)
        self.start_btn.pack(side='left', padx=(0, 6))
        self.stop_btn = ttk.Button(top, text='停止并整理日志', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left', padx=(0, 12))
        ttk.Button(top, text='打开日志目录', command=self.open_dir).pack(side='left')
        ttk.Label(top, text='单条正文上限').pack(side='left', padx=(16, 3))
        ttk.Entry(top, textvariable=self.limit, width=5).pack(side='left')
        ttk.Label(top, text='MB，0=不限').pack(side='left', padx=3)
        markers = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        markers.pack(fill='x')
        ttk.Label(markers, text='操作前标记：').pack(side='left')
        for label in ('大厅', '开包', '单卡强化', '一键强化', '组阵容'):
            ttk.Button(markers, text=label, command=lambda name=label: self.mark(name)).pack(side='left', padx=(0, 5))
        ttk.Label(markers, textvariable=self.phase, foreground='#246').pack(side='left', padx=8)
        info = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        info.pack(fill='x')
        ttk.Label(info, textvariable=self.status).pack(anchor='w')
        ttk.Label(info, textvariable=self.output, foreground='#555').pack(anchor='w')
        description = ttk.Label(info, text='在 Chrome 中手动登录、开包、强化和组阵容。上面的标记按钮只给日志分段。\n'
                  '记录全域请求/响应、请求头、接口正文、WebSocket 和 SSE；不拦截统计，不自动点击或修改游戏。\n'
                  'Cookie/令牌等已知字段脱敏；静态图片、字体等记录元数据，超出正文上限会明确标记。',
                  foreground='#555', justify='left')
        description.pack(anchor='w', fill='x', pady=(6, 0))
        description.bind('<Configure>', lambda event: description.configure(wraplength=max(200, event.width)))
        frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        frame.pack(fill='both', expand=True)
        self.log = tk.Text(frame, wrap='none', font=('Consolas', 9), state='disabled')
        bar = ttk.Scrollbar(frame, orient='vertical', command=self.log.yview)
        xbar = ttk.Scrollbar(frame, orient='horizontal', command=self.log.xview)
        self.log.configure(yscrollcommand=bar.set, xscrollcommand=xbar.set)
        self.log.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        xbar.grid(row=1, column=0, sticky='ew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

    def _log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        self._line_count += 1
        if self._line_count > 3000:
            self.log.delete('1.0', '501.0')
            self._line_count -= 500
        self.log.see('end')
        self.log.configure(state='disabled')

    def _receive(self, kind, value):
        self.events.put((kind, value))

    def _pump(self):
        for _ in range(100):
            try:
                kind, record = self.events.get_nowait()
            except queue.Empty:
                break
            if kind in self._counts:
                self._counts[kind] += 1
            rid = record.get('request_id', '')
            url = record.get('url', '')
            if url:
                parsed = urlsplit(url)
                url = (parsed.netloc + parsed.path)[:250]
            if kind == 'request':
                self._log('%s → %s %s [%s]' % (rid, record.get('method'), url, record.get('phase')))
            elif kind == 'response':
                self._log('%s ← %s %s' % (rid, record.get('status'), url))
            elif kind == 'response_body' and record.get('body', {}).get('format') in ('unavailable', 'omitted'):
                body = record['body']
                if body.get('reason') != 'static-resource':
                    self._log('%s 正文未保存：%s' % (rid, body.get('reason')))
            elif kind == 'marker':
                self._log('──── ' + record.get('label', '') + ' ────')
            elif kind in ('websocket_frame', 'eventsource_message'):
                self._log('%s %s' % (kind, record.get('direction', 'received')))
            elif kind == 'capture_error':
                self._log('采集异常：%s %s' % (record.get('source'), record.get('error')))
            elif kind == 'ready':
                self.output.set(record['path'])
                self._log('驱动：' + record['driver'])
            elif kind == 'browser':
                self._log('浏览器：' + record['executable'])
            elif kind == 'listening':
                self.status.set('监听中，可在 Chrome 手动操作')
            elif kind in ('notice', 'fatal'):
                self._log(record['text'] + ' ' + record.get('error', ''))
            elif kind == 'closing':
                self.status.set('正在关闭浏览器并整理日志…')
            elif kind == 'finished':
                self.archive = record.get('archive')
                self.status.set('已停止。请求 %d，响应 %d，WS 帧 %d，采集异常 %d' % tuple(self._counts.values()))
                if self.archive:
                    self.output.set(self.archive)
                    self._log('可回传的压缩包：' + self.archive)
                self.start_btn.configure(state='normal')
                self.stop_btn.configure(state='disabled')
        self._pump_job = self.root.after(100, self._pump)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        rid = self.rid.get().strip()
        try:
            limit = int(self.limit.get())
            if not rid.isdigit() or limit < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning('输入错误', '房间号需为数字，正文上限需为非负整数。')
            return
        self._counts = dict.fromkeys(self._counts, 0)
        self.phase.set('大厅')
        self.session = ObserverSession(rid, body_limit=limit * 1024 * 1024, on_event=self._receive)
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.status.set('正在打开游戏页面并安装监听…')
        self.thread = threading.Thread(target=self.session.run, name='api-observer', daemon=True)
        self.thread.start()

    def stop(self):
        if self.session:
            self.session.stop()
            self.stop_btn.configure(state='disabled')
            self.status.set('正在停止并整理日志…')

    def mark(self, label):
        if self.thread and self.thread.is_alive() and self.session:
            self.phase.set(label)
            self.session.mark(label)

    def open_dir(self):
        os.makedirs(LOGDIR, exist_ok=True)
        if hasattr(os, 'startfile'):
            os.startfile(LOGDIR)
        else:
            self._log(LOGDIR)

    def close(self):
        if self.thread and self.thread.is_alive():
            self._closing = True
            self.stop()
            self.root.after(100, self.close)
        else:
            self.root.after_cancel(self._pump_job)
            self.root.destroy()


def main():
    root = tk.Tk()
    ApiLabApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
