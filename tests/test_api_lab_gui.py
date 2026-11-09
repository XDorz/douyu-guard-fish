import threading
import tkinter as tk
import unittest
from unittest.mock import patch

from api_lab_gui import ApiLabApp


class GuiObserverTests(unittest.TestCase):
    def test_start_stop_restart_creates_fresh_session_and_local_markers(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        sessions = []

        class Session:
            def __init__(self, rid, body_limit, on_event):
                self.stopped = threading.Event()
                self.marks = []
                self.on_event = on_event
                sessions.append(self)

            def run(self):
                self.on_event('listening', {})
                self.stopped.wait(3)
                self.on_event('finished', {'archive': 'logs/test.zip'})

            def stop(self):
                self.stopped.set()

            def mark(self, value):
                self.marks.append(value)

        with patch('api_lab_gui.ObserverSession', Session):
            app = ApiLabApp(root)
            try:
                for _ in range(2):
                    app.start_btn.invoke()
                    self.assertTrue(app.thread.is_alive())
                    app.mark('开包')
                    self.assertEqual(sessions[-1].marks, ['开包'])
                    app.stop_btn.invoke()
                    app.thread.join(2)
                    self.assertFalse(app.thread.is_alive())
                    root.after_cancel(app._pump_job)
                    app._pump()
                    self.assertEqual(str(app.start_btn['state']), 'normal')
                    self.assertEqual(str(app.stop_btn['state']), 'disabled')
                self.assertEqual(len(sessions), 2)
                self.assertIsNot(sessions[0], sessions[1])
                self.assertEqual(app.archive, 'logs/test.zip')
            finally:
                app.close()


if __name__ == '__main__':
    unittest.main()
