import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from ccs_monitor.models import DeviceSnapshot
from ccs_monitor.rtsp_video import RtspVideoWidget, build_rtsp_url


class FakePlayer(QObject):
    mediaStatusChanged = Signal(object)
    playbackStateChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.sources = []
        self.play_count = 0
        self.stop_count = 0

    def setSource(self, source: QUrl):
        self.sources.append(source.toString())

    def play(self):
        self.play_count += 1

    def stop(self):
        self.stop_count += 1


class RtspVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.player = FakePlayer()
        self.widget = RtspVideoWidget(lambda _output, _parent: self.player)
        self.device = DeviceSnapshot("UAV_001", "Test UAV", "UAV", ip_address="192.168.1.25")

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def test_builds_ipv4_and_ipv6_urls(self):
        self.assertEqual(build_rtsp_url("192.168.1.25"), "rtsp://192.168.1.25:8554/usb_cam")
        self.assertEqual(build_rtsp_url("2001:db8::1"), "rtsp://[2001:db8::1]:8554/usb_cam")
        with self.assertRaises(ValueError):
            build_rtsp_url("camera.local")

    def test_toggle_starts_and_stops_stream(self):
        self.widget.set_device(self.device)
        self.widget.stream_switch.setChecked(True)
        self.assertEqual(self.player.sources[-1], "rtsp://192.168.1.25:8554/usb_cam")
        self.assertEqual(self.player.play_count, 1)
        self.widget.stream_switch.setChecked(False)
        self.assertGreaterEqual(self.player.stop_count, 1)
        self.assertEqual(self.widget.status_label.text(), "视频流已关闭")

    def test_same_snapshot_update_does_not_restart_stream(self):
        self.widget.set_device(self.device)
        self.widget.start_stream()
        self.widget.set_device(self.device)
        self.assertEqual(self.player.play_count, 1)

    def test_device_change_stops_active_stream(self):
        self.widget.set_device(self.device)
        self.widget.start_stream()
        changed = DeviceSnapshot("UAV_002", "Other", "UAV", ip_address="192.168.1.26")
        self.widget.set_device(changed)
        self.assertFalse(self.widget.stream_switch.isChecked())
        self.assertGreaterEqual(self.player.stop_count, 1)

    def test_playing_and_error_states(self):
        states = []
        self.widget.stream_state_changed.connect(states.append)
        self.widget.set_device(self.device)
        self.widget.start_stream()
        self.player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.widget.video_stack.currentWidget(), self.widget.video_output)
        self.player.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "connection refused")
        self.assertFalse(self.widget.stream_switch.isChecked())
        self.assertIn("connection refused", self.widget.status_label.text())
        self.assertIn("playing", states)
        self.assertIn("error", states)


if __name__ == "__main__":
    unittest.main()
