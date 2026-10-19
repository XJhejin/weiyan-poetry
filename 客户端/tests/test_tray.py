"""Unread tray lifecycle: whole-logo frames without removing the tray slot."""
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tray


class TrayTests(unittest.TestCase):
    def make_tray(self):
        with patch.object(tray.pystray, 'Icon'), patch.object(tray, 'WindowEffects'):
            item = tray.Tray(MagicMock(), Path(tray.__file__).parent / 'ui/assets/logo.png',
                             {'native_notifications': True}, MagicMock())
        item.foreground = lambda: False
        return item

    def test_whole_logo_blinks_without_removing_click_target(self):
        item = self.make_tray()
        item.notify({'body': '测试消息', 'sender': {'nickname': '掇花者'}})
        self.assertIs(item.icon.icon, item.image)
        self.assertIn('掇花者：测试消息', item.icon.title)
        item.stopped.wait = MagicMock(side_effect=[False, False, False, True])
        recorded = []
        type(item.icon).icon = property(lambda _: recorded[-1] if recorded else item.image,
                                        lambda _, value: recorded.append(value))
        item.pulse()
        self.assertEqual(recorded, [item.blank, item.image, item.blank])
        self.assertIsNone(item.blank.getbbox())
        self.assertEqual(item.blank.size, item.image.size)
        self.assertNotIn('visible', item.icon.__dict__)
        item.clear_unread()
        self.assertEqual(item.unread, 0)
        self.assertIs(item.icon.icon, item.image)
        self.assertEqual(item.icon.title, '未言诗集')

    def test_foreground_and_open_clear_preview_and_stop_blinking(self):
        item = self.make_tray()
        message = {'attachment': {'kind': 'video', 'name': '片段.mp4'}, 'sender': {'nickname': '联系人'}}
        item.notify(message)
        self.assertIn('[视频] 片段.mp4', item.icon.title)
        item.foreground = lambda: True
        item.stopped.wait = MagicMock(side_effect=[False, True])
        item.pulse()
        self.assertEqual(item.unread, 0)
        self.assertIs(item.icon.icon, item.image)
        item.notify(message)
        self.assertEqual(item.unread, 0)
        item.foreground = lambda: False
        item.notify(message)
        item.show()
        self.assertEqual(item.unread, 0)
        item.effects.show.assert_called_once()

    def test_burst_messages_merge_alerts_and_global_disable_stops_blinking(self):
        item = self.make_tray()
        message = {'body': '提醒', 'sender': {'nickname': '联系人'}}
        with patch.object(tray.time, 'monotonic', side_effect=[10, 11, 14]):
            for _ in range(3): item.notify(message)
        self.assertEqual(item.unread, 3)
        self.assertEqual(item.icon.notify.call_count, 2)
        item.config['native_notifications'] = False
        item.clear_unread()
        item.notify(message)
        self.assertEqual(item.unread, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
