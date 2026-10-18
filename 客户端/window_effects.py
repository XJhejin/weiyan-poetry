"""Short native Windows fades, honoring the system's animation preference."""
import ctypes
import sys
import time


class WindowEffects:
    def __init__(self, window, close_window=False):
        self.window, self.timer, self.closing = window, None, False
        self.enabled = sys.platform == 'win32'
        if self.enabled:
            flag = ctypes.c_int(1)
            ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(flag), 0)
            self.enabled = bool(flag.value)
            window.events.before_show += self.prepare
            window.events.shown += self.appear
            if close_window:
                window.events.closing += self.close

    def prepare(self):
        if self.enabled:
            self.window.native.Opacity = 0.02

    def _run(self, target, complete=None, reveal=False):
        native = self.window.native
        if not self.enabled or native is None:
            if reveal:
                self.window.show()
                self.window.restore()
            if complete:
                complete()
            return
        from System import Action
        from System.Windows.Forms import Timer, FormWindowState

        def start():
            if self.timer:
                self.timer.Stop()
                self.timer.Dispose()
            if reveal:
                if not native.Visible:
                    native.Opacity = 0.02
                native.Show()
                if native.WindowState == FormWindowState.Minimized:
                    native.WindowState = FormWindowState.Normal
                native.Activate()
            beginning, stamp = float(native.Opacity), time.perf_counter()
            timer = Timer()
            timer.Interval = 16
            self.timer = timer

            def tick(*args):
                t = min(1.0, (time.perf_counter() - stamp) / (0.19 if target else 0.15))
                eased = 1 - (1 - t) ** 3
                native.Opacity = max(0.0, min(1.0, beginning + (target - beginning) * eased))
                if t >= 1:
                    timer.Stop()
                    timer.Dispose()
                    if self.timer is timer:
                        self.timer = None
                    if complete:
                        complete()
            timer.Tick += tick
            timer.Start()
        if native.InvokeRequired:
            native.Invoke(Action(start))
        else:
            start()

    def appear(self):
        self._run(1.0)

    def show(self):
        self._run(1.0, reveal=True)

    def hide(self):
        def hidden():
            self.window.native.Hide()
            self.window.native.Opacity = 1.0
        if self.enabled:
            self._run(0.0, hidden)
        else:
            self.window.hide()

    def close(self):
        if self.closing:
            return True
        self.closing = True
        self._run(0.0, self.window.destroy)
        return False
