from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TASK_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "task_system.json"


class TaskSystemConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSystemConfig:
    schema_version: int
    protocol_id: str
    bind_host: str
    status_port: int
    device_control_port: int
    max_datagram_bytes: int
    chunk_payload_bytes: int
    retry_seconds: float
    max_attempts: int
    heartbeat_timeout_seconds: float
    group_start_delay_seconds: float
    group_ack_deadline_seconds: float
    max_waypoints_per_subtask: int
    max_compressed_bytes: int


def load_task_system_config(path: str | Path = DEFAULT_TASK_CONFIG_PATH) -> TaskSystemConfig:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != 2:
            raise ValueError("schema_version 必须为 2")
        network = payload["network"]
        transport = payload["transport"]
        limits = payload["limits"]
        config = TaskSystemConfig(
            int(payload["schema_version"]), str(payload["protocol_id"]), str(network["bind_host"]), int(network["status_port"]),
            int(network["device_control_port"]), int(network["max_datagram_bytes"]),
            int(transport["chunk_payload_bytes"]), float(transport["retry_seconds"]),
            int(transport["max_attempts"]), float(transport["heartbeat_timeout_seconds"]),
            float(transport["group_start_delay_seconds"]), float(transport["group_ack_deadline_seconds"]),
            int(limits["max_waypoints_per_subtask"]), int(limits["max_compressed_bytes"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskSystemConfigError(f"任务系统配置无效：{exc}") from exc
    if not config.protocol_id:
        raise TaskSystemConfigError("protocol_id 不能为空")
    if not all(1 <= port <= 65535 for port in (config.status_port, config.device_control_port)):
        raise TaskSystemConfigError("UDP 端口无效")
    if not 512 <= config.max_datagram_bytes <= 65507:
        raise TaskSystemConfigError("数据报大小无效")
    if not 64 <= config.chunk_payload_bytes < config.max_datagram_bytes:
        raise TaskSystemConfigError("分片 payload 大小无效")
    if min(config.retry_seconds, config.heartbeat_timeout_seconds, config.group_start_delay_seconds) <= 0:
        raise TaskSystemConfigError("超时参数必须大于零")
    if config.max_attempts < 1 or config.max_waypoints_per_subtask < 2 or config.max_compressed_bytes < 1024:
        raise TaskSystemConfigError("重试或资源限制无效")
    return config
