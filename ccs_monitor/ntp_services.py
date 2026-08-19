from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot

from .ntp_config import NtpServerConfig


NTP_PACKET_SIZE = 48
NTP_UNIX_EPOCH_OFFSET = 2_208_988_800


def encode_ntp_timestamp(unix_seconds: float) -> bytes:
    whole_seconds = int(unix_seconds)
    fraction = int((unix_seconds - whole_seconds) * (1 << 32))
    return struct.pack(
        "!II",
        (whole_seconds + NTP_UNIX_EPOCH_OFFSET) & 0xFFFFFFFF,
        fraction & 0xFFFFFFFF,
    )


def build_ntp_response(
    request: bytes,
    config: NtpServerConfig,
    received_at: float,
    transmitted_at: float,
) -> bytes | None:
    if len(request) < NTP_PACKET_SIZE or request[0] & 0x07 != 3:
        return None
    version = (request[0] >> 3) & 0x07
    if version not in (3, 4):
        return None

    response = bytearray(NTP_PACKET_SIZE)
    response[0] = (version << 3) | 4
    response[1] = config.stratum
    response[2] = request[2]
    response[3] = config.precision & 0xFF
    response[8:12] = struct.pack("!I", 1 << 10)  # 1/64 s root dispersion.
    response[12:16] = config.reference_id.encode("ascii").ljust(4, b"\0")
    response[16:24] = encode_ntp_timestamp(received_at - 1.0)
    response[24:32] = request[40:48]
    response[32:40] = encode_ntp_timestamp(received_at)
    response[40:48] = encode_ntp_timestamp(transmitted_at)
    return bytes(response)


class NtpServerService(QObject):
    status_changed = Signal(str, bool)

    def __init__(
        self,
        config: NtpServerConfig,
        clock: Callable[[], float] = time.time,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._clock = clock
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._socket: socket.socket | None = None

    @Slot()
    def start(self) -> None:
        if not self.config.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="ccs-ntp-server", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket = sock
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.settimeout(0.2)
            sock.bind((self.config.bind_host, self.config.port))
            self.status_changed.emit(
                f"NTP Server 运行中 · UDP {self.config.port} · stratum {self.config.stratum}", True
            )
            while not self._stop_event.is_set():
                try:
                    request, peer = sock.recvfrom(512)
                    received_at = self._clock()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise
                response = build_ntp_response(
                    request, self.config, received_at, self._clock()
                )
                if response is not None:
                    sock.sendto(response, peer)
        except OSError as exc:
            self.status_changed.emit(f"NTP Server 启动失败：{exc}", False)
        finally:
            try:
                sock.close()
            finally:
                self._socket = None

    @Slot()
    def stop(self, timeout_seconds: float = 2.0) -> None:
        self._stop_event.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout_seconds)
