"""Windows tray lifecycle and privacy-preserving native balloon notifications."""
import ctypes
import os
from pathlib import Path
import threading
from PIL import Image, ImageDraw
import pystray


class Tray:
    def __init__(self, window, icon, config, save_config):
        self.window, self.config, self.save_config = window, config, save_config
        self.exiting, self.hidden, self.unread = False, False, 0
        self.image = Image.open(icon).convert('RGBA')
        self.icon = pystray.Icon('WeiyanPoetry', self.image, '未言诗集', menu=pystray.Menu(
            pystray.MenuItem('打开未言诗集', self.show, default=True),
            pystray.MenuItem('新消息提醒', self.toggle, checked=lambda item: self.config.get('native_notifications', True)),
            pystray.Menu.SEPARATOR, pystray.MenuItem('退出未言诗集', self.exit)))
        window.events.closing += self.closing

    def start(self):
        threading.Thread(target=self.icon.run, daemon=True, name='windows-tray').start()

    def closing(self):
        if self.exiting:
            return True
        self.hidden = True
        self.window.hide()
        return False

    def show(self, *args):
        self.hidden = False
        self.unread = 0
        self.icon.icon = self.image
        self.icon.title = '未言诗集'
        self.window.show()
        self.window.restore()

    def toggle(self, *args):
        self.save_config(not self.config.get('native_notifications', True))
        self.icon.update_menu()

    def notify(self, message):
        foreground = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        if not self.hidden and pid.value == os.getpid():
            return
        self.unread += 1
        badge = self.image.copy()
        draw = ImageDraw.Draw(badge)
        size = badge.width
        draw.ellipse((size*.61, size*.02, size*.98, size*.39), fill='#cf7294', outline='#fff8fa', width=max(1,size//50))
        self.icon.icon = badge
        self.icon.title = f'未言诗集 · {self.unread} 条新消息'
        if self.config.get('native_notifications', True):
            self.icon.notify('收到新的加密消息，点击托盘图标查看。', '未言诗集')

    def exit(self, *args):
        self.exiting = True
        self.icon.stop()
        self.window.destroy()

    def stop(self):
        self.icon.stop()
