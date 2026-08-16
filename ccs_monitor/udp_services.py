from __future__ import annotations

import socket
import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .udp_config import UdpTelemetryConfig
from .udp_store import UdpTelemetryStore


class UdpReceiverThread(QThread):
    datagram_received = Signal(bytes, str, int)
    receiver_status = Signal(str, bool)

    def __init__(self, config: UdpTelemetryConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._stop_event = threading.Event()
        self._socket: socket.socket | None = None

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket = sock
        try:
            sock.settimeout(0.2)
            sock.bind((self.config.bind_host, self.config.port))
            self.receiver_status.emit(f"UDP 遥测已监听 {self.config.bind_host}:{self.config.port}", True)
            while not self._stop_event.is_set():
                try:
                    data, peer = sock.recvfrom(self.config.max_datagram_bytes + 1)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise
                self.datagram_received.emit(data, peer[0], peer[1])
        except OSError as exc:
            self.receiver_status.emit(f"UDP 遥测启动失败：{exc}", False)
        finally:
            try:
                sock.close()
            finally:
                self._socket = None

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self.wait(2000)


class UdpMonitoringRuntime(QObject):
    def __init__(self, config: UdpTelemetryConfig, store: UdpTelemetryStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.receiver = UdpReceiverThread(config, self)
        self.receiver.datagram_received.connect(store.process_datagram)
        self.receiver.receiver_status.connect(store.set_module_status)

    @Slot()
    def start(self) -> None:
        if not self.receiver.isRunning():
            self.receiver.start()

    @Slot()
    def stop(self) -> None:
        self.receiver.stop()
