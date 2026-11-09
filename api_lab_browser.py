"""监听版只复用路径和 Chrome 发现逻辑；不实例化任何挂机控制器。"""
import os
import queue
import threading
from urllib.parse import quote

from bot import DATA_DIR, PROFILE, LOGDIR, GAME_URL, DEFAULT_RID, chrome_candidates
from browser_compat import pick_driver
from api_capture import ApiCapture


class ObserverSession:
    def __init__(self, rid=DEFAULT_RID, logdir=LOGDIR, profile=PROFILE, body_limit=16 * 1024 * 1024,
                 on_event=None):
        self.rid, self.logdir, self.profile = str(rid), logdir, profile
        self.body_limit = body_limit
        self.on_event = on_event or (lambda *_: None)
        self.stop_event = threading.Event()
        self.commands = queue.Queue()
        self.capture = None
        self.archive = None

    def stop(self):
        self.stop_event.set()

    def mark(self, label):
        # 只写本地日志，可以从 GUI 线程立即标记，避免下一次事件轮询前的操作错分组。
        if self.capture is not None:
            self.capture.mark(label)
        else:
            self.commands.put(str(label))

    def _launch(self, pw):
        candidates = chrome_candidates()
        if not candidates:
            raise RuntimeError('未找到 Chrome/Edge，请安装浏览器，或设置 CHROME_PATH')
        # 不杀进程、不清理 profile 锁，正在使用同一目录时由 Chrome 拒绝启动。
        # 固定使用第一个候选，避免 profile 被占用时尝试用另一个浏览器接管。
        executable = candidates[0][0]
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=self.profile, executable_path=executable, headless=False,
            no_viewport=True, args=['--no-first-run', '--no-default-browser-check'])
        return ctx, executable

    def run(self):
        os.makedirs(self.logdir, exist_ok=True)
        os.makedirs(self.profile, exist_ok=True)
        context = None
        try:
            name, factory, timeout_error = pick_driver()
            self.capture = ApiCapture(self.logdir, on_log=self.on_event, body_limit=self.body_limit)
            self.on_event('ready', {'path': self.capture.path, 'driver': name})
            with factory() as pw:
                try:
                    if self.stop_event.is_set():
                        return
                    context, executable = self._launch(pw)
                    self.capture.install(context)
                    page = context.pages[0] if context.pages else context.new_page()
                    closed = threading.Event()
                    context.on('close', lambda *_: closed.set())
                    self.on_event('browser', {'executable': executable})
                    if not self.stop_event.is_set():
                        try:
                            page.goto(GAME_URL.format(rid=quote(self.rid, safe='')),
                                      wait_until='commit', timeout=15000)
                        except Exception as exc:
                            self.on_event('notice', {'text': '首次导航未完成，可在浏览器内手动继续。',
                                                     'error': type(exc).__name__})
                    self.on_event('listening', {})
                    while not self.stop_event.is_set() and not closed.is_set():
                        try:
                            while True:
                                self.capture.mark(self.commands.get_nowait())
                        except queue.Empty:
                            pass
                        # 同步驱动必须在等待期间持续分派事件；time.sleep 会漏掉手动操作期间的回调。
                        try:
                            context.wait_for_event('close', timeout=250)
                        except timeout_error:
                            pass
                        except Exception:
                            if not closed.is_set():
                                raise
                finally:
                    if context is not None:
                        self.on_event('closing', {})
                        try:
                            context.close()
                        except Exception:
                            pass
                        # close 触发的最后一批卸载请求也参与记录，然后再写汇总和压缩包。
        except Exception as exc:
            self.on_event('fatal', {'error': type(exc).__name__,
                'text': '监听已停止。若 Chrome 无法启动，请先关闭使用同一登录目录的助手或浏览器。'})
        finally:
            if self.capture is not None:
                try:
                    self.capture.close()
                    self.archive = self.capture.zip_path
                except Exception as exc:
                    self.on_event('fatal', {'error': type(exc).__name__, 'text': '日志整理失败，原目录内的文件仍可查看。'})
            self.on_event('finished', {'archive': self.archive})
