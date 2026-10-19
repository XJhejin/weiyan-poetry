"""Windows tray lifecycle and privacy-preserving native balloon notifications."""
import ctypes
import json
import os
import threading
import time
from PIL import Image
import pystray
from window_effects import WindowEffects


class MessageIcon(pystray.Icon):
    """pystray 0.19.5 does not dispatch Windows balloon clicks itself."""
    def _on_notify(self, wparam, lparam):
        if lparam == 0x405:  # NIN_BALLOONUSERCLICK (WM_USER + 5)
            self()
        else:
            return super()._on_notify(wparam, lparam)


class Tray:
    def __init__(self, window, icon, config, save_config):
        self.window, self.config, self.save_config = window, config, save_config
        self.exiting, self.hidden, self.unread = False, False, 0
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.effects = WindowEffects(window)
        self.image = Image.open(icon).convert('RGBA')
        # Keep the notification-area slot registered and clickable in both frames.
        # Toggling Icon.visible would remove/reinsert it and disturb its position.
        self.blank = Image.new('RGBA', self.image.size, (0, 0, 0, 0))
        self.bright = True
        self.last_alert = None
        self.latest_conversation = None
        self.icon = MessageIcon('WeiyanPoetry', self.image, '未言诗集', menu=pystray.Menu(
            pystray.MenuItem('打开未言诗集', self.show, default=True),
            pystray.MenuItem('新消息提醒', self.toggle, checked=lambda item: self.config.get('native_notifications', True)),
            pystray.Menu.SEPARATOR, pystray.MenuItem('退出未言诗集', self.exit)))
        window.events.closing += self.closing

    def start(self):
        threading.Thread(target=self.icon.run, daemon=True, name='windows-tray').start()
        threading.Thread(target=self.pulse, daemon=True, name='unread-tray-animation').start()

    def closing(self):
        if self.exiting:
            return True
        self.hidden = True
        self.effects.hide()
        return False

    def show(self, *args):
        self.hidden = False
        with self.lock:
            conversation = self.latest_conversation if self.unread else None
        self.clear_unread()
        self.effects.show()
        if conversation:
            def open_message():
                try:
                    self.window.evaluate_js('window.weiyanOpenNotification && window.weiyanOpenNotification(' + json.dumps(conversation) + ')')
                except Exception:
                    pass  # Opening the window must still work during UI startup.
            threading.Thread(target=open_message, daemon=True, name='open-notified-conversation').start()

    def foreground(self):
        foreground = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        return not self.hidden and pid.value == os.getpid()

    def clear_unread(self):
        with self.lock:
            self.unread = 0
            self.bright = True
            self.icon.icon = self.image
            self.icon.title = '未言诗集'
            self.latest_conversation = None
            self.last_alert = None
            self.icon.remove_notification()

    def pulse(self):
        while not self.stopped.wait(.6):
            with self.lock:
                if self.unread:
                    if self.foreground():
                        self.clear_unread()
                    else:
                        self.bright = not self.bright
                        self.icon.icon = self.image if self.bright else self.blank

    def toggle(self, *args):
        self.save_config(not self.config.get('native_notifications', True))
        if not self.config.get('native_notifications', True):
            self.clear_unread()
            self.icon.remove_notification()
        self.icon.update_menu()

    def notify(self, message):
        if self.foreground() or not self.config.get('native_notifications', True):
            return
        with self.lock:
            self.unread += 1
            self.latest_conversation = message.get('conversation_id')
            self.bright = True
            self.icon.icon = self.image
            preview = message.get('body', '').strip()
            if not preview:
                file = message.get('attachment')
                preview = ('[视频] ' if file.get('kind') == 'video' else '[文件] ') + file['name'] if file else '[图片]' if message.get('image') else '新消息'
            preview = ' '.join(preview.split())
            title = f"未言诗集 · {self.unread} 条新消息\n{message['sender'].get('nickname', '联系人')}：{preview}"
            self.icon.title = title.encode('utf-16-le')[:246].decode('utf-16-le', errors='ignore')
            now = time.monotonic()
            alert = self.last_alert is None or now - self.last_alert >= 3
            if alert:
                self.last_alert = now
        if alert:
            self.icon.notify('收到新的加密消息，点击托盘图标查看。', '未言诗集')

    def exit(self, *args):
        self.exiting = True
        self.stopped.set()
        self.effects._run(0.0, lambda: (self.icon.stop(), self.window.destroy()))

    def stop(self):
        self.stopped.set()
        self.icon.stop()
