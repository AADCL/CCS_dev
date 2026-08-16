from __future__ import annotations

import ipaddress
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


@dataclass(frozen=True)
class PingResult:
    ip_address: str
    reachable: bool
    message: str


def build_ping_command(ip_address: str, platform_name: str | None = None) -> list[str]:
    parsed = ipaddress.ip_address(ip_address)
    platform_name = platform_name or sys.platform
    if platform_name.startswith("win"):
        return ["ping", "-n", "1", "-w", "1500", str(parsed)]
    command = ["ping"]
    if parsed.version == 6:
        command.append("-6")
    command.extend(["-c", "1", "-W", "2", str(parsed)])
    return command


def ping_ip(
    ip_address: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform_name: str | None = None,
) -> PingResult:
    try:
        command = build_ping_command(ip_address, platform_name)
    except ValueError:
        return PingResult(ip_address, False, "IP 地址格式无效")
    try:
        completed = runner(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return PingResult(ip_address, False, "连接测试超时")
    except OSError as exc:
        return PingResult(ip_address, False, f"无法执行 ping：{exc}")
    if completed.returncode == 0:
        return PingResult(ip_address, True, "设备可达")
    return PingResult(ip_address, False, "设备不可达")


class PingWorkerSignals(QObject):
    finished = Signal(object)


class PingWorker(QRunnable):
    def __init__(self, ip_address: str) -> None:
        super().__init__()
        self.ip_address = ip_address
        self.signals = PingWorkerSignals()

    def run(self) -> None:
        self.signals.finished.emit(ping_ip(self.ip_address))

