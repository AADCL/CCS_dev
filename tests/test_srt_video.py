import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ccs_monitor.device_dialogs import NewDeviceDialog
from ccs_monitor.models import DeviceSnapshot
from ccs_monitor.srt_video import (
    SrtEndpoint, SrtFfmpegReceiver, SrtVideoConfig, SrtVideoConfigError,
    SrtVideoWidget, build_srt_url, load_srt_video_config, protocols_include_srt,
)


class FakeReceiver(QObject):
    frame_ready = Signal(QImage)
    state_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self.starts = []
        self.stop_count = 0

    def start(self, endpoint):
        self.starts.append(endpoint)
        self.state_changed.emit("connecting")

    def stop(self, emit_state=True):
        self.stop_count += 1
        if emit_state:
            self.state_changed.emit("stopped")


class SrtVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_builds_ipv4_and_ipv6_urls_with_microsecond_latency(self):
        self.assertEqual(
            build_srt_url("192.168.1.25"),
            "srt://192.168.1.25:9000?mode=caller&transtype=live&latency=120000",
        )
        self.assertEqual(
            build_srt_url(SrtEndpoint("2001:db8::1", 9010, 250)),
            "srt://[2001:db8::1]:9010?mode=caller&transtype=live&latency=250000",
        )
        with self.assertRaises(ValueError):
            build_srt_url("camera.local")

    def test_endpoint_validates_boundaries(self):
        SrtEndpoint("127.0.0.1", 1, 20)
        SrtEndpoint("127.0.0.1", 65535, 8000)
        with self.assertRaises(ValueError):
            SrtEndpoint("127.0.0.1", 0, 120)
        with self.assertRaises(ValueError):
            SrtEndpoint("127.0.0.1", 9000, 8001)

    def test_new_device_dialog_uses_srt_defaults_without_spin_buttons(self):
        dialog = NewDeviceDialog(lambda _device_id: False)
        self.assertEqual(dialog.srt_port_input.value(), 9000)
        self.assertEqual(dialog.srt_latency_input.value(), 120)
        self.assertEqual(dialog.srt_port_input.buttonSymbols(), dialog.srt_port_input.ButtonSymbols.NoButtons)
        self.assertEqual(dialog.srt_latency_input.buttonSymbols(), dialog.srt_latency_input.ButtonSymbols.NoButtons)
        dialog.close()

    def test_protocol_check_only_accepts_srt_input(self):
        supported = "Supported file protocols:\nInput:\n  file\n  srt\nOutput:\n  file\n"
        output_only = "Supported file protocols:\nInput:\n  file\nOutput:\n  srt\n"
        self.assertTrue(protocols_include_srt(supported))
        self.assertFalse(protocols_include_srt(output_only))

    def test_ffmpeg_arguments_use_raw_rgba_pipe_without_shell(self):
        config = SrtVideoConfig(output_width=320, output_height=240, output_fps=15)
        receiver = SrtFfmpegReceiver(config)
        arguments = receiver.ffmpeg_arguments(SrtEndpoint("127.0.0.1", 9001, 80))
        self.assertIn("srt://127.0.0.1:9001?mode=caller&transtype=live&latency=80000", arguments)
        self.assertEqual(arguments[-3:], ["-f", "rawvideo", "pipe:1"])
        self.assertIn("rgba", arguments)
        self.assertIn("-nostdin", arguments)
        self.assertFalse(any("shell" in value.lower() for value in arguments))

    def test_fragmented_rawvideo_emits_only_latest_complete_frame(self):
        config = SrtVideoConfig(output_width=2, output_height=1)
        receiver = SrtFfmpegReceiver(config)
        images = []
        receiver.frame_ready.connect(images.append)
        first = bytes((255, 0, 0, 255, 0, 255, 0, 255))
        second = bytes((0, 0, 255, 255, 255, 255, 255, 255))
        receiver.feed_rawvideo(first[:3])
        self.assertEqual(images, [])
        receiver.feed_rawvideo(first[3:] + second)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].pixelColor(0, 0).blue(), 255)

    def test_widget_uses_device_endpoint_and_stops_on_change(self):
        fake = FakeReceiver()
        widget = SrtVideoWidget(fake)
        first = DeviceSnapshot("A", "One", "UAV", ip_address="192.168.1.10",
                               srt_port=9100, srt_latency_ms=200)
        widget.set_device(first)
        widget.start_stream()
        self.assertEqual(fake.starts[-1], SrtEndpoint("192.168.1.10", 9100, 200))
        self.assertIn("latency=200000", widget.url_label.text())
        before = fake.stop_count
        widget.set_device(DeviceSnapshot("B", "Two", "UGV", ip_address="192.168.1.11"))
        self.assertGreater(fake.stop_count, before)
        self.assertFalse(widget.stream_switch.isChecked())
        widget.close()

    def test_config_rejects_absolute_ffmpeg_path_and_resolves_relative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "srt.json"
            payload = {"schema_version": 1, "ffmpeg_executable": "C:/ffmpeg/bin/ffmpeg.exe"}
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(SrtVideoConfigError):
                load_srt_video_config(path)
            payload["ffmpeg_executable"] = "tools/ffmpeg.exe"
            path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_srt_video_config(path)
            self.assertTrue(Path(config.ffmpeg_executable).is_absolute())

    def test_unexpected_exit_retries_are_bounded_and_stop_cancels_timer(self):
        receiver = SrtFfmpegReceiver(SrtVideoConfig(retry_delay_ms=50, max_retries=2))
        states = []
        errors = []
        receiver.state_changed.connect(states.append)
        receiver.error_occurred.connect(errors.append)
        receiver._requested = True
        receiver._schedule_retry_or_fail("failed")
        self.assertTrue(receiver._retry_timer.isActive())
        receiver._retry_timer.stop()
        receiver._schedule_retry_or_fail("failed")
        receiver._retry_timer.stop()
        receiver._schedule_retry_or_fail("failed")
        self.assertEqual(states.count("retrying"), 2)
        self.assertEqual(errors, ["failed"])
        receiver._requested = True
        receiver._retry_timer.start()
        receiver.stop()
        self.assertFalse(receiver._retry_timer.isActive())


if __name__ == "__main__":
    unittest.main()
