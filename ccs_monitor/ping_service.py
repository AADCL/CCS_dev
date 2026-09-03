from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from .external_process import external_environment

from .device_address import (
    DeviceAddressError,
    DeviceAddressResolutionError,
    is_mdns_hostname,
    normalize_device_address,
    resolve_device_address,
)


@dataclass(frozen=True)
class PingResult:
    ip_address: str
    reachable: bool
    message: str
    resolved_address: str | None = None


def build_ping_command(ip_address: str, platform_name: str | None = None) -> list[str]:
    target = normalize_device_address(ip_address)
    platform_name = platform_name or sys.platform
    if platform_name.startswith("win"):
        return ["ping", "-n", "1", "-w", "1500", target]
    command = ["ping"]
    if ":" in target:
        command.append("-6")
    command.extend(["-c", "1", "-W", "2", target])
    return command


def ping_ip(
    ip_address: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    platform_name: str | None = None,
    resolver: Callable[..., object] | None = None,
) -> PingResult:
    try:
        normalized = normalize_device_address(ip_address)
    except DeviceAddressError as exc:
        return PingResult(ip_address, False, str(exc))
    target = normalized
    if is_mdns_hostname(normalized):
        try:
            target = resolve_device_address(normalized, resolver=resolver)
        except DeviceAddressResolutionError as exc:
            return PingResult(normalized, False, str(exc))
    command = build_ping_command(target, platform_name)
    try:
        completed = runner(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            shell=False,
            env=external_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return PingResult(normalized, False, "连接测试超时", target)
    except OSError as exc:
        return PingResult(normalized, False, f"无法执行 ping：{exc}", target)
    if completed.returncode == 0:
        message = f"设备可达（已解析为 {target}）" if target != normalized else "设备可达"
        return PingResult(normalized, True, message, target)
    message = f"设备不可达（已解析为 {target}）" if target != normalized else "设备不可达"
    return PingResult(normalized, False, message, target)


class PingWorkerSignals(QObject):
    finished = Signal(object)


class PingWorker(QRunnable):
    def __init__(self, ip_address: str) -> None:
        super().__init__()
        self.ip_address = ip_address
        self.signals = PingWorkerSignals()

    def run(self) -> None:
        self.signals.finished.emit(ping_ip(self.ip_address))

