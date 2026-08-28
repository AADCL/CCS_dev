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
    resolve_ffmpeg_executable,
)
from ccs_monitor.styles import ThemeMode, theme_palette


class FakeReceiver(QObject):
    frame_ready = Signal(QImage)
    state_changed = Signal(str)
    error_occurred = Signal(str)
    diagnostic = Signal(str)

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
        self.assertEqual(
            build_srt_url("camera.local"),
            "srt://camera.local:9000?mode=caller&transtype=live&latency=120000",
        )
        with self.assertRaises(ValueError):
            build_srt_url("example.com")

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

    def test_new_device_dialog_accepts_mdns_address(self):
        dialog = NewDeviceDialog(lambda _device_id: False)
        dialog.name_input.setText("NRC device")
        dialog.id_input.setText("FGV-017")
        dialog.ip_input.setText("nrc17.local")
        self.assertIsNone(dialog._validate_fields())
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
        states = []
        receiver.frame_ready.connect(images.append)
        receiver.state_changed.connect(states.append)
        first = bytes((255, 0, 0, 255, 0, 255, 0, 255))
        second = bytes((0, 0, 255, 255, 255, 255, 255, 255))
        receiver.feed_rawvideo(first[:3])
        self.assertEqual(images, [])
        receiver.feed_rawvideo(first[3:] + second)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].pixelColor(0, 0).blue(), 255)
        self.assertEqual(states.count("playing"), 1)

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

    def test_playing_state_keeps_video_canvas_visible_and_logs_diagnostics(self):
        fake = FakeReceiver()
        widget = SrtVideoWidget(fake)
        events = []
        widget.stream_event.connect(lambda state, message: events.append((state, message)))
        widget._show_frame(QImage(2, 2, QImage.Format.Format_RGBA8888))
        fake.state_changed.emit("playing")
        self.assertIs(widget.video_stack.currentWidget(), widget.video_output)
        fake.diagnostic.emit("decoder warning")
        self.assertEqual(events[-1][0], "diagnostic")
        widget.close()

    def test_collapsed_video_expands_when_stream_starts(self):
        fake = FakeReceiver()
        widget = SrtVideoWidget(fake)
        widget.set_collapsible(True)
        widget.set_device(DeviceSnapshot("A", "One", "UGV", ip_address="192.168.1.10"))
        widget.set_collapsed(True)
        self.assertTrue(widget._collapsed)
        self.assertEqual(widget.collapse_button.property("appIconName"), "expand")
        self.assertEqual(widget.collapse_button.property("appIconRotation"), 90)
        widget.set_theme(theme_palette(ThemeMode.DAY))
        self.assertEqual(widget.collapse_button.property("appIconMode"), "day")
        self.assertTrue(widget.video_body.isHidden())
        widget.start_stream()
        self.assertFalse(widget._collapsed)
        self.assertFalse(widget.video_body.isHidden())
        self.assertFalse(widget.collapse_button.isEnabled())
        widget.stop_stream()
        self.assertTrue(widget.collapse_button.isEnabled())
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

    def test_resolves_ffmpeg_from_process_and_windows_user_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process_bin = root / "process-bin"
            user_bin = root / "user-bin"
            process_bin.mkdir()
            user_bin.mkdir()
            executable_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            process_ffmpeg = process_bin / executable_name
            user_ffmpeg = user_bin / executable_name
            process_ffmpeg.touch()
            user_ffmpeg.touch()

            resolved = resolve_ffmpeg_executable(
                executable_name, root, environ={"PATH": str(process_bin)},
                user_path="", machine_path="", local_app_data="",
            )
            self.assertEqual(Path(resolved), process_ffmpeg.resolve())

            resolved = resolve_ffmpeg_executable(
                executable_name, root, environ={"PATH": str(root / "stale")},
                user_path=str(user_bin), machine_path="", local_app_data="",
            )
            self.assertEqual(Path(resolved), user_ffmpeg.resolve())

    def test_resolves_ffmpeg_from_standard_local_app_data_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg = root / "Programs" / "ffmpeg" / "bin" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.touch()
            resolved = resolve_ffmpeg_executable(
                "ffmpeg", root, environ={"PATH": ""}, user_path="",
                machine_path="", local_app_data=root,
            )
            self.assertEqual(Path(resolved), ffmpeg.resolve())

    def test_missing_ffmpeg_error_includes_attempted_executable(self):
        receiver = SrtFfmpegReceiver(SrtVideoConfig(ffmpeg_executable="missing-ffmpeg"))
        errors = []
        receiver.error_occurred.connect(errors.append)
        receiver._requested = True
        receiver._on_process_error(receiver._process_factory.ProcessError.FailedToStart)
        self.assertEqual(errors, ["未找到 FFmpeg：missing-ffmpeg"])

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

    def test_stream_retry_resets_first_frame_state(self):
        receiver = SrtFfmpegReceiver(SrtVideoConfig())
        receiver._requested = True
        receiver._endpoint = SrtEndpoint("127.0.0.1", 9000)
        receiver._playing = True
        receiver._first_frame_timed_out = True
        receiver._start_stream_process()
        self.assertFalse(receiver._playing)
        self.assertFalse(receiver._first_frame_timed_out)
        self.assertTrue(receiver._first_frame_timer.isActive())
        receiver.stop()


if __name__ == "__main__":
    unittest.main()
