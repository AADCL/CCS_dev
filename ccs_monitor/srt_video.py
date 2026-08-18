from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QProcess, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .models import DeviceSnapshot


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

    @property
    def frame_bytes(self) -> int:
        return self.output_width * self.output_height * 4


@dataclass(frozen=True)
class SrtEndpoint:
    ip_address: str
    port: int = 9000
    latency_ms: int = 120

    def __post_init__(self) -> None:
        ipaddress.ip_address(self.ip_address.strip())
        if not 1 <= self.port <= 65535:
            raise ValueError("SRT port must be between 1 and 65535")
        if not 20 <= self.latency_ms <= 8000:
            raise ValueError("SRT latency must be between 20 and 8000 ms")


def build_srt_url(endpoint: SrtEndpoint | str, port: int = 9000,
                  latency_ms: int = 120) -> str:
    endpoint = endpoint if isinstance(endpoint, SrtEndpoint) else SrtEndpoint(endpoint, port, latency_ms)
    address = ipaddress.ip_address(endpoint.ip_address.strip())
    host = f"[{address}]" if address.version == 6 else str(address)
    query = urlencode({
        "mode": "caller",
        "transtype": "live",
        "latency": endpoint.latency_ms * 1000,
    })
    return f"srt://{host}:{endpoint.port}?{query}"


def load_srt_video_config(path: str | Path | None = None) -> SrtVideoConfig:
    application_root = Path(__file__).resolve().parents[1]
    config_path = Path(path) if path is not None else application_root / "config" / "srt_video.json"
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
    if "/" in executable or "\\" in executable or executable_path.parent != Path("."):
        executable = str((application_root / executable_path).resolve())
    values = {name: int(payload.get(name, default)) for name, default in (
        ("output_width", 640), ("output_height", 480), ("output_fps", 30),
        ("probe_size_bytes", 1_000_000), ("analyze_duration_us", 1_000_000),
        ("connect_timeout_us", 5_000_000), ("retry_delay_ms", 1000),
        ("max_retries", 3),
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
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(self.config.retry_delay_ms)
        self._retry_timer.timeout.connect(self._start_stream_process)

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
        self._buffer.clear()
        self._stderr.clear()
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
        self._process.start(self.config.ffmpeg_executable, ["-hide_banner", "-protocols"])

    def _start_stream_process(self) -> None:
        if not self._requested or self._endpoint is None:
            return
        self._checking_protocols = False
        self._buffer.clear()
        self._stderr.clear()
        self._process = self._new_process()
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        args = self.ffmpeg_arguments(self._endpoint)
        self.state_changed.emit("connecting")
        self._process.start(self.config.ffmpeg_executable, args)

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
        self.state_changed.emit("playing")

    def _read_stderr(self) -> None:
        if self._process is not None:
            self._stderr.extend(bytes(self._process.readAllStandardError()))

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if not self._requested:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self._fail("未找到系统 FFmpeg，请安装带 SRT 支持的 FFmpeg")

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
        message = (
            "FFmpeg 输出帧尺寸异常"
            if self._buffer else self.classify_error(self._stderr.decode("utf-8", "replace"))
        )
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
        self._build()
        self.receiver.frame_ready.connect(self._show_frame)
        self.receiver.state_changed.connect(self._on_state)
        self.receiver.error_occurred.connect(lambda message: self._show_status(f"播放失败：{message}"))
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
        layout.addLayout(header)
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
        layout.addWidget(self.video_stack, 1)
        self.url_label = QLabel("SRT 地址将在选择设备后生成")
        self.url_label.setObjectName("videoUrl")
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.setWordWrap(True)
        layout.addWidget(self.url_label)

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
            self.url_label.setText("设备未配置 IP，无法生成 SRT 地址")
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
        self._set_switch(True)
        self._show_status("正在检查 FFmpeg 与连接 SRT 视频流...")
        self.receiver.start(self._endpoint)

    def stop_stream(self) -> None:
        self.receiver.stop()
        self._set_switch(False)
        self._show_status("视频流已关闭")

    def _toggle_stream(self, enabled: bool) -> None:
        self.start_stream() if enabled else self.stop_stream()

    def _show_frame(self, image: QImage) -> None:
        self.video_output.set_frame(image)
        self.video_stack.setCurrentWidget(self.video_output)

    def _on_state(self, state: str) -> None:
        labels = {
            "checking": "正在检查 FFmpeg SRT 支持...",
            "connecting": "正在连接 SRT 视频流...",
            "retrying": "视频连接中断，正在重试...",
        }
        if state in labels:
            self._show_status(labels[state])
        elif state == "error":
            self._set_switch(False)
        self.stream_state_changed.emit(state)

    def _show_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.video_stack.setCurrentWidget(self.placeholder)

    def _set_switch(self, checked: bool) -> None:
        self.stream_switch.blockSignals(True)
        self.stream_switch.setChecked(checked)
        self.stream_switch.blockSignals(False)
