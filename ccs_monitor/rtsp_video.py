from __future__ import annotations

import ipaddress
from typing import Callable

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .models import DeviceSnapshot


RTSP_PORT = 8554
RTSP_PATH = "/usb_cam"


def build_rtsp_url(ip_address: str) -> str:
    address = ipaddress.ip_address(ip_address.strip())
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"rtsp://{host}:{RTSP_PORT}{RTSP_PATH}"


class RtspVideoWidget(QFrame):
    stream_state_changed = Signal(str)

    def __init__(
        self,
        player_factory: Callable[[QVideoWidget, QWidget], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("videoPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumSize(320, 250)
        self._device_key: tuple[str, str] | None = None
        self._stream_url = ""
        self._stopping = False
        self._build(player_factory)

    def _build(self, player_factory) -> None:
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
        self.video_stack.setObjectName("videoStack")
        self.video_stack.setMinimumHeight(190)
        self.placeholder = QWidget()
        placeholder_layout = QVBoxLayout(self.placeholder)
        placeholder_layout.setContentsMargins(18, 18, 18, 18)
        placeholder_layout.addStretch()
        self.status_label = QLabel("视频流已关闭")
        self.status_label.setObjectName("videoStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        placeholder_layout.addWidget(self.status_label)
        placeholder_layout.addStretch()
        self.video_output = QVideoWidget()
        self.video_output.setObjectName("videoOutput")
        self.video_output.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.video_stack.addWidget(self.placeholder)
        self.video_stack.addWidget(self.video_output)
        layout.addWidget(self.video_stack, 1)

        self.url_label = QLabel("RTSP 地址将在选择设备后生成")
        self.url_label.setObjectName("videoUrl")
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.setWordWrap(True)
        layout.addWidget(self.url_label)

        if player_factory is None:
            self.player = QMediaPlayer(self)
            self.player.setVideoOutput(self.video_output)
        else:
            self.player = player_factory(self.video_output, self)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.errorOccurred.connect(self._on_error)

    def set_device(self, device: DeviceSnapshot | None) -> None:
        key = None if device is None else (device.device_id, device.ip_address)
        if key == self._device_key:
            return
        self.stop_stream()
        self._device_key = key
        self._stream_url = ""
        if device is None or not device.ip_address:
            self.url_label.setText("设备未配置 IP，无法生成 RTSP 地址")
            self.stream_switch.setEnabled(False)
            return
        try:
            self._stream_url = build_rtsp_url(device.ip_address)
        except ValueError:
            self.url_label.setText("设备 IP 无效，无法生成 RTSP 地址")
            self.stream_switch.setEnabled(False)
            return
        self.url_label.setText(self._stream_url)
        self.stream_switch.setEnabled(True)

    def start_stream(self) -> None:
        if not self._stream_url:
            return
        self._set_switch(True)
        self._show_status("正在连接视频流...")
        self.player.setSource(QUrl(self._stream_url))
        self.player.play()
        self.stream_state_changed.emit("connecting")

    def stop_stream(self) -> None:
        self._stopping = True
        try:
            self.player.stop()
            self.player.setSource(QUrl())
            self._set_switch(False)
            self._show_status("视频流已关闭")
            self.stream_state_changed.emit("stopped")
        finally:
            self._stopping = False

    def _toggle_stream(self, enabled: bool) -> None:
        if enabled:
            self.start_stream()
        else:
            self.stop_stream()

    def _on_media_status(self, status) -> None:
        if self._stopping or not self.stream_switch.isChecked():
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadingMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        ):
            self._show_status("正在连接视频流...")
        elif status == QMediaPlayer.MediaStatus.NoMedia:
            self._show_status("未检测到视频流")
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._fail("RTSP 视频流无效或不可访问")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._fail("RTSP 视频流已结束")

    def _on_playback_state(self, state) -> None:
        if self._stopping or not self.stream_switch.isChecked():
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.video_stack.setCurrentWidget(self.video_output)
            self.stream_state_changed.emit("playing")

    def _on_error(self, _error, error_text: str) -> None:
        if self._stopping or not self.stream_switch.isChecked():
            return
        self._fail(error_text or "RTSP 视频播放失败")

    def _fail(self, message: str) -> None:
        self._stopping = True
        try:
            self.player.stop()
            self.player.setSource(QUrl())
            self._set_switch(False)
            self._show_status(f"播放失败：{message}")
            self.stream_state_changed.emit("error")
        finally:
            self._stopping = False

    def _show_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.video_stack.setCurrentWidget(self.placeholder)

    def _set_switch(self, checked: bool) -> None:
        self.stream_switch.blockSignals(True)
        self.stream_switch.setChecked(checked)
        self.stream_switch.blockSignals(False)
