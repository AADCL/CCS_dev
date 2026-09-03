from __future__ import annotations

from .external_process import start_external_process

import json
import os
import shutil
from dataclasses import dataclass
from .runtime_paths import application_root
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QProcess, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .app_icons import apply_button_icon
from .device_address import format_device_address_for_url, normalize_device_address
from .models import DeviceSnapshot
from .styles import ThemeMode, ThemePalette, theme_palette


class SrtVideoConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SrtVideoConfig:
    ffmpeg_executable: str = "ffmpeg"
    output_width: int = 640
    output_height: int = 480
    output_fps: int = 30
    probe_size_bytes: int = 1_000_000
    analyze_duration_us: int = 1_000_000
    connect_timeout_us: int = 5_000_000
    retry_delay_ms: int = 1000
    max_retries: int = 3
    first_frame_timeout_ms: int = 7000
    stderr_limit_bytes: int = 32768

    @property
    def frame_bytes(self) -> int:
        return self.output_width * self.output_height * 4


@dataclass(frozen=True)
class SrtEndpoint:
    ip_address: str
    port: int = 9000
    latency_ms: int = 120

    def __post_init__(self) -> None:
        normalize_device_address(self.ip_address)
        if not 1 <= self.port <= 65535:
            raise ValueError("SRT port must be between 1 and 65535")
        if not 20 <= self.latency_ms <= 8000:
            raise ValueError("SRT latency must be between 20 and 8000 ms")


def build_srt_url(endpoint: SrtEndpoint | str, port: int = 9000,
                  latency_ms: int = 120) -> str:
    endpoint = endpoint if isinstance(endpoint, SrtEndpoint) else SrtEndpoint(endpoint, port, latency_ms)
    host = format_device_address_for_url(endpoint.ip_address)
    query = urlencode({
        "mode": "caller",
        "transtype": "live",
        "latency": endpoint.latency_ms * 1000,
    })
    return f"srt://{host}:{endpoint.port}?{query}"


def _windows_environment_path(scope: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        if scope == "user":
            root = winreg.HKEY_CURRENT_USER
            key_name = "Environment"
        else:
            root = winreg.HKEY_LOCAL_MACHINE
            key_name = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        with winreg.OpenKey(root, key_name) as key:
            value, _ = winreg.QueryValueEx(key, "Path")
        return os.path.expandvars(str(value))
    except (ImportError, OSError):
        return ""


def resolve_ffmpeg_executable(
    configured: str,
    application_root: str | Path,
    environ: Mapping[str, str] | None = None,
    user_path: str | None = None,
    machine_path: str | None = None,
    local_app_data: str | Path | None = None,
) -> str:
    """Resolve FFmpeg without relying on the GUI process' inherited PATH."""
    def existing_file(candidate: Path) -> str | None:
        try:
            return str(candidate.resolve()) if candidate.is_file() else None
        except OSError:
            return None

    configured = configured.strip()
    configured_path = Path(configured)
    if "/" in configured or "\\" in configured or configured_path.parent != Path("."):
        return str((Path(application_root) / configured_path).resolve())

    if configured == "ffmpeg":
        for relative in ("tools/ffmpeg/bin/ffmpeg.exe", "tools/ffmpeg/bin/ffmpeg"):
            match = existing_file(Path(application_root) / relative)
            if match:
                return match
    environment = os.environ if environ is None else environ
    process_path = environment.get("PATH", "")
    direct_match = shutil.which(configured, path=process_path)
    if direct_match:
        return str(Path(direct_match).resolve())

    if os.name == "nt" or user_path is not None or machine_path is not None or local_app_data is not None:
        resolved_user_path = _windows_environment_path("user") if user_path is None else user_path
        resolved_machine_path = _windows_environment_path("machine") if machine_path is None else machine_path
        merged_path = os.pathsep.join(filter(None, (
            process_path, resolved_user_path, resolved_machine_path,
        )))
        merged_match = shutil.which(configured, path=merged_path)
        if merged_match:
            return str(Path(merged_match).resolve())

        local_root = local_app_data or environment.get("LOCALAPPDATA")
        if local_root:
            candidate = Path(local_root) / "Programs" / "ffmpeg" / "bin" / "ffmpeg.exe"
            match = existing_file(candidate)
            if match:
                return match

        app_root = Path(application_root)
        for candidate in (
            app_root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            app_root / "tools" / "ffmpeg.exe",
        ):
            match = existing_file(candidate)
            if match:
                return match

    return configured


def load_srt_video_config(path: str | Path | None = None) -> SrtVideoConfig:
    app_root = application_root()
    config_path = Path(path) if path is not None else app_root / "config" / "srt_video.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SrtVideoConfigError(f"SRT 视频配置读取失败：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise SrtVideoConfigError("SRT 视频配置 schema_version 必须为 1")
    executable = str(payload.get("ffmpeg_executable", "ffmpeg")).strip()
    if not executable:
        raise SrtVideoConfigError("FFmpeg 命令不能为空")
    executable_path = Path(executable)
    if executable_path.is_absolute():
        raise SrtVideoConfigError("FFmpeg 静态路径必须相对软件根目录配置")
    executable = resolve_ffmpeg_executable(executable, app_root)
    values = {name: int(payload.get(name, default)) for name, default in (
        ("output_width", 640), ("output_height", 480), ("output_fps", 30),
        ("probe_size_bytes", 1_000_000), ("analyze_duration_us", 1_000_000),
        ("connect_timeout_us", 5_000_000), ("retry_delay_ms", 1000),
        ("max_retries", 3), ("first_frame_timeout_ms", 7000),
        ("stderr_limit_bytes", 32768),
    )}
    if any(values[name] <= 0 for name in values if name != "max_retries"):
        raise SrtVideoConfigError("SRT 视频尺寸、帧率和超时参数必须大于 0")
    if values["max_retries"] < 0:
        raise SrtVideoConfigError("max_retries 不能小于 0")
    return SrtVideoConfig(ffmpeg_executable=executable, **values)


def protocols_include_srt(output: str) -> bool:
    inputs = output.split("Output:", 1)[0]
    return "srt" in {line.strip() for line in inputs.splitlines()}


class SrtFfmpegReceiver(QObject):
    frame_ready = Signal(QImage)
    state_changed = Signal(str)
    error_occurred = Signal(str)
    diagnostic = Signal(str)

    def __init__(self, config: SrtVideoConfig | None = None,
                 process_factory: Callable[[QObject], QProcess] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config or load_srt_video_config()
        self._process_factory = process_factory or QProcess
        self._process: QProcess | None = None
        self._endpoint: SrtEndpoint | None = None
        self._buffer = bytearray()
        self._requested = False
        self._protocol_checked = False
        self._checking_protocols = False
        self._retries = 0
        self._stderr = bytearray()
        self._playing = False
        self._first_frame_timed_out = False
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(self.config.retry_delay_ms)
        self._retry_timer.timeout.connect(self._start_stream_process)
        self._first_frame_timer = QTimer(self)
        self._first_frame_timer.setSingleShot(True)
        self._first_frame_timer.setInterval(self.config.first_frame_timeout_ms)
        self._first_frame_timer.timeout.connect(self._on_first_frame_timeout)

    @property
    def endpoint(self) -> SrtEndpoint | None:
        return self._endpoint

    def start(self, endpoint: SrtEndpoint) -> None:
        self.stop(emit_state=False)
        self._endpoint = endpoint
        self._requested = True
        self._retries = 0
        if self._protocol_checked:
            self._start_stream_process()
        else:
            self._start_protocol_check()

    def stop(self, emit_state: bool = True) -> None:
        self._requested = False
        self._checking_protocols = False
        self._retry_timer.stop()
        self._first_frame_timer.stop()
        self._buffer.clear()
        self._stderr.clear()
        self._playing = False
        self._first_frame_timed_out = False
        process, self._process = self._process, None
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1000)
        if emit_state:
            self.state_changed.emit("stopped")

    def _new_process(self) -> QProcess:
        process = self._process_factory(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.errorOccurred.connect(self._on_process_error)
        process.finished.connect(self._on_process_finished)
        return process

    def _start_protocol_check(self) -> None:
        if not self._requested:
            return
        self._checking_protocols = True
        self._process = self._new_process()
        self.state_changed.emit("checking")
        start_external_process(self._process, self.config.ffmpeg_executable, ["-hide_banner", "-protocols"])

    def _start_stream_process(self) -> None:
        if not self._requested or self._endpoint is None:
            return
        self._checking_protocols = False
        self._buffer.clear()
        self._stderr.clear()
        self._playing = False
        self._first_frame_timed_out = False
        self._process = self._new_process()
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        args = self.ffmpeg_arguments(self._endpoint)
        self.state_changed.emit("connecting")
        start_external_process(self._process, self.config.ffmpeg_executable, args)
        self._first_frame_timer.start()

    def ffmpeg_arguments(self, endpoint: SrtEndpoint) -> list[str]:
        vf = (
            f"scale={self.config.output_width}:{self.config.output_height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={self.config.output_width}:{self.config.output_height}:"
            "(ow-iw)/2:(oh-ih)/2,fps=" + str(self.config.output_fps)
        )
        return [
            "-hide_banner", "-nostdin", "-loglevel", "warning", "-fflags", "nobuffer",
            "-flags", "low_delay", "-analyzeduration", str(self.config.analyze_duration_us),
            "-probesize", str(self.config.probe_size_bytes), "-rw_timeout",
            str(self.config.connect_timeout_us), "-i", build_srt_url(endpoint), "-an",
            "-vf", vf, "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1",
        ]

    def _read_stdout(self) -> None:
        if self._process is None:
            return
        self.feed_rawvideo(bytes(self._process.readAllStandardOutput()))

    def feed_rawvideo(self, data: bytes) -> None:
        self._buffer.extend(data)
        frame_size = self.config.frame_bytes
        latest: bytes | None = None
        while len(self._buffer) >= frame_size:
            latest = bytes(self._buffer[:frame_size])
            del self._buffer[:frame_size]
        if latest is None:
            return
        image = QImage(latest, self.config.output_width, self.config.output_height,
                       self.config.output_width * 4, QImage.Format.Format_RGBA8888).copy()
        self.frame_ready.emit(image)
        if not self._playing:
            self._playing = True
            self._first_frame_timer.stop()
            self.state_changed.emit("playing")

    def _read_stderr(self) -> None:
        if self._process is not None:
            chunk = bytes(self._process.readAllStandardError())
            self._stderr.extend(chunk)
            if len(self._stderr) > self.config.stderr_limit_bytes:
                del self._stderr[:-self.config.stderr_limit_bytes]
            text = chunk.decode("utf-8", "replace").strip()
            if text:
                self.diagnostic.emit(text[-2000:])

    def _on_first_frame_timeout(self) -> None:
        if not self._requested or self._playing or self._process is None:
            return
        self._first_frame_timed_out = True
        self.diagnostic.emit("FFmpeg 已启动，但首帧等待超时")
        self._process.kill()

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if not self._requested:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self._fail(f"未找到 FFmpeg：{self.config.ffmpeg_executable}")

    def _on_process_finished(self, exit_code: int, _exit_status) -> None:
        process = self.sender()
        if process is not self._process:
            return
        if self._checking_protocols:
            self._checking_protocols = False
            output = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
            if exit_code != 0:
                self._fail("无法检查 FFmpeg 协议支持")
                return
            if not protocols_include_srt(output):
                self._fail("当前 FFmpeg 未启用 SRT 输入协议")
                return
            self._protocol_checked = True
            self._process = None
            self._start_stream_process()
            return
        if not self._requested:
            return
        self._first_frame_timer.stop()
        message = ("SRT 首帧等待超时" if self._first_frame_timed_out else
            "FFmpeg 输出帧尺寸异常"
            if self._buffer else self.classify_error(self._stderr.decode("utf-8", "replace"))
        )
        self._first_frame_timed_out = False
        self._schedule_retry_or_fail(message)

    def _schedule_retry_or_fail(self, message: str) -> None:
        if not self._requested:
            return
        if self._retries < self.config.max_retries:
            self._retries += 1
            self.state_changed.emit("retrying")
            self._retry_timer.start()
        else:
            self._fail(message)

    @staticmethod
    def classify_error(stderr: str) -> str:
        text = stderr.lower()
        if "timed out" in text or "timeout" in text:
            return "SRT 连接超时"
        if "refused" in text or "reject" in text:
            return "端侧拒绝 SRT 连接"
        if "invalid data" in text or "decode" in text or "h264" in text:
            return "SRT 视频解码失败"
        return "SRT 视频进程异常退出"

    def _fail(self, message: str) -> None:
        self._requested = False
        self._retry_timer.stop()
        self.error_occurred.emit(message)
        self.state_changed.emit("error")


class SrtFrameCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self.setMinimumHeight(190)

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#050A10"))
        if self._image.isNull():
            return
        size = self._image.size()
        size.scale(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = self.rect()
        target.setSize(size)
        target.moveCenter(self.rect().center())
        painter.drawImage(target, self._image)


class SrtVideoWidget(QFrame):
    stream_state_changed = Signal(str)
    stream_event = Signal(str, str)

    def __init__(self, receiver: SrtFfmpegReceiver | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("videoPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumSize(320, 250)
        self._configuration_error: str | None = None
        if receiver is None:
            try:
                receiver = SrtFfmpegReceiver(parent=self)
            except SrtVideoConfigError as exc:
                self._configuration_error = str(exc)
                receiver = SrtFfmpegReceiver(SrtVideoConfig(), parent=self)
        self.receiver = receiver
        self._endpoint: SrtEndpoint | None = None
        self._device_key: tuple | None = None
        self._last_event_state: str | None = None
        self._stream_started = False
        self._collapsible = False
        self._collapsed = False
        self.theme_palette = theme_palette(ThemeMode.NIGHT)
        self._build()
        self.receiver.frame_ready.connect(self._show_frame)
        self.receiver.state_changed.connect(self._on_state)
        self.receiver.error_occurred.connect(self._on_error)
        diagnostic = getattr(self.receiver, "diagnostic", None)
        if diagnostic is not None:
            diagnostic.connect(self._on_diagnostic)
        if self._configuration_error:
            self._show_status(self._configuration_error)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("实时视频")
        title.setObjectName("panelTitle")
        header.addWidget(title)
        header.addStretch()
        self.stream_switch = QCheckBox("视频流")
        self.stream_switch.setObjectName("videoSwitch")
        self.stream_switch.setEnabled(False)
        self.stream_switch.toggled.connect(self._toggle_stream)
        header.addWidget(self.stream_switch)
        self.collapse_button = QPushButton()
        self.collapse_button.setObjectName("videoCollapseButton")
        self.collapse_button.setToolTip("收起视频")
        self.collapse_button.setAccessibleName("收起视频")
        self.collapse_button.setVisible(False)
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        header.addWidget(self.collapse_button)
        layout.addLayout(header)
        self.video_body = QWidget()
        body_layout = QVBoxLayout(self.video_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        self.video_stack = QStackedWidget()
        self.placeholder = QWidget()
        placeholder_layout = QVBoxLayout(self.placeholder)
        placeholder_layout.addStretch()
        self.status_label = QLabel("视频流已关闭")
        self.status_label.setObjectName("videoStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        placeholder_layout.addWidget(self.status_label)
        placeholder_layout.addStretch()
        self.video_output = SrtFrameCanvas()
        self.video_output.setObjectName("videoOutput")
        self.video_stack.addWidget(self.placeholder)
        self.video_stack.addWidget(self.video_output)
        body_layout.addWidget(self.video_stack, 1)
        self.url_label = QLabel("SRT 地址将在选择设备后生成")
        self.url_label.setObjectName("videoUrl")
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.setWordWrap(True)
        body_layout.addWidget(self.url_label)
        layout.addWidget(self.video_body, 1)
        self._refresh_collapse_icon()

    def set_theme(self, palette: ThemePalette) -> None:
        self.theme_palette = palette
        self._refresh_collapse_icon()
        self.update()

    def _refresh_collapse_icon(self) -> None:
        apply_button_icon(
            self.collapse_button,
            "expand" if self._collapsed else "close",
            self.theme_palette,
            rotation=90,
            text="",
        )

    def set_collapsible(self, enabled: bool) -> None:
        self._collapsible = bool(enabled)
        self.collapse_button.setVisible(self._collapsible)
        if not self._collapsible:
            self.set_collapsed(False)

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed) and self._collapsible and not self.stream_switch.isChecked()
        self._collapsed = collapsed
        self.video_body.setVisible(not collapsed)
        self.collapse_button.setToolTip("展开视频" if collapsed else "收起视频")
        self.collapse_button.setAccessibleName(self.collapse_button.toolTip())
        self._refresh_collapse_icon()
        self.setMinimumHeight(0 if collapsed else 250)
        self.setMaximumHeight(self.sizeHint().height() if collapsed else 16777215)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_device(self, device: DeviceSnapshot | None) -> None:
        key = None if device is None else (
            device.device_id, device.ip_address, device.srt_port, device.srt_latency_ms,
        )
        if key == self._device_key:
            return
        self.stop_stream()
        self._device_key = key
        self._endpoint = None
        if self._configuration_error:
            self.url_label.setText(self._configuration_error)
            self.stream_switch.setEnabled(False)
            return
        if device is None or not device.ip_address:
            self.url_label.setText("设备未配置地址，无法生成 SRT 地址")
            self.stream_switch.setEnabled(False)
            return
        try:
            self._endpoint = SrtEndpoint(device.ip_address, device.srt_port, device.srt_latency_ms)
        except ValueError:
            self.url_label.setText("设备 SRT 配置无效")
            self.stream_switch.setEnabled(False)
            return
        self.url_label.setText(build_srt_url(self._endpoint))
        self.stream_switch.setEnabled(True)

    def start_stream(self) -> None:
        if self._endpoint is None:
            return
        self.set_collapsed(False)
        self._set_switch(True)
        self._stream_started = True
        self._show_status("正在检查 FFmpeg 与连接 SRT 视频流...")
        self.receiver.start(self._endpoint)

    def stop_stream(self) -> None:
        self.receiver.stop()
        self._set_switch(False)
        self._show_status("视频流已关闭")

    def _toggle_stream(self, enabled: bool) -> None:
        if enabled:
            self.set_collapsed(False)
        self.start_stream() if enabled else self.stop_stream()

    def _show_frame(self, image: QImage) -> None:
        self.video_output.set_frame(image)
        self.video_stack.setCurrentWidget(self.video_output)

    def _on_state(self, state: str) -> None:
        labels = {
            "checking": "正在检查 FFmpeg SRT 支持...",
            "connecting": "正在连接 SRT 视频流...",
            "retrying": "视频连接中断，正在重试...",
            "playing": "SRT 视频流已开始播放",
            "stopped": "SRT 视频流已停止",
        }
        if state == "playing":
            self.video_stack.setCurrentWidget(self.video_output)
        elif state in labels:
            self._show_status(labels[state])
        elif state == "error":
            self._set_switch(False)
        should_emit = state != "stopped" or self._stream_started
        if state in labels and state != self._last_event_state and should_emit:
            self._last_event_state = state
            self.stream_event.emit(state, labels[state])
        if state == "stopped":
            self._stream_started = False
        self.stream_state_changed.emit(state)

    def _on_error(self, message: str) -> None:
        text = f"播放失败：{message}"
        self._show_status(text)
        self._last_event_state = "error"
        self.stream_event.emit("error", text)

    def _on_diagnostic(self, message: str) -> None:
        self.stream_event.emit("diagnostic", f"FFmpeg：{message}")

    def _show_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.video_stack.setCurrentWidget(self.placeholder)

    def _set_switch(self, checked: bool) -> None:
        self.stream_switch.blockSignals(True)
        self.stream_switch.setChecked(checked)
        self.stream_switch.blockSignals(False)
        self.collapse_button.setEnabled(not checked)
