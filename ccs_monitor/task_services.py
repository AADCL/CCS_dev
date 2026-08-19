from __future__ import annotations

import socket
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from PySide6.QtCore import QObject, Signal

from .models import DeviceSnapshot, utc_now
from .task_config import TaskSystemConfig
from .task_models import (
    DeviceSubtask, TaskDefinition, TaskEventLevel, TaskExecutionSnapshot, TaskExecutionStatus,
)
from .task_protocol import EncodedSubtask, TaskEnvelope, TaskProtocol, TaskProtocolError
from .task_repository import TaskRepository, TaskRepositoryError


@dataclass
class _PendingCommand:
    envelope: TaskEnvelope
    peer_ip: str
    task_id: str
    execution_id: str | None
    device_id: str
    attempts: int = 0
    last_sent: float = 0.0


@dataclass
class _Transfer:
    task: TaskDefinition
    subtask: DeviceSubtask
    encoded: EncodedSubtask
    request_id: str
    execution_id: str | None


class TaskExecutionService(QObject):
    availability_changed = Signal(bool, str)
    transfer_updated = Signal(str, str, str)
    execution_updated = Signal(object)
    event_received = Signal(str, object)
    protocol_warning = Signal(str)

    def __init__(
        self,
        config: TaskSystemConfig,
        repository: TaskRepository,
        device_lookup: Callable[[str], DeviceSnapshot | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        socket_factory: Callable[..., socket.socket] = socket.socket,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.repository = repository
        self.device_lookup = device_lookup
        self.protocol = TaskProtocol(config)
        self.clock = clock
        self.socket_factory = socket_factory
        self.available = False
        self.module_message = "UDP 任务模块尚未启动"
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingCommand] = {}
        self._transfers: dict[tuple[str, str], _Transfer] = {}
        self._executions: dict[str, TaskExecutionSnapshot] = {}
        self._device_execution: dict[str, str] = {}
        self._last_heartbeat: dict[tuple[str, str], float] = {}
        self._sequence = 0

    def start(self) -> None:
        if self._running.is_set():
            return
        udp_socket = None
        try:
            udp_socket = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.bind((self.config.bind_host, self.config.status_port))
            udp_socket.settimeout(0.1)
        except OSError as exc:
            if udp_socket:
                udp_socket.close()
            self.module_message = f"UDP {self.config.status_port} 端口绑定失败：{exc}"
            self.available = False
            self.availability_changed.emit(False, self.module_message)
            return
        self._socket = udp_socket
        self._running.set()
        self.available = True
        self.module_message = f"UDP 任务监听 {self.config.bind_host}:{self.config.status_port}"
        self.availability_changed.emit(True, self.module_message)
        self._thread = threading.Thread(target=self._run, name="ccs-task-control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        for execution_id in tuple(self._executions):
            self.stop_execution(execution_id, "应用退出")
        self._running.clear()
        udp_socket, self._socket = self._socket, None
        if udp_socket:
            udp_socket.close()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        self.available = False

    def device_active(self, device_id: str) -> bool:
        folded = device_id.casefold()
        return any(key.casefold() == folded for key in self._device_execution)

    def deliver_subtask(self, task: TaskDefinition, subtask: DeviceSubtask, execution_id: str | None = None) -> None:
        self._require_available_device(subtask.device_id)
        if not subtask.is_valid or subtask.revision <= 0:
            raise ValueError("子任务尚未保存有效轨迹")
        encoded = self.protocol.encode_subtask(task, subtask)
        request_id = uuid.uuid4().hex
        transfer = _Transfer(task, subtask, encoded, request_id, execution_id)
        with self._lock:
            self._transfers[(task.task_id, subtask.device_id)] = transfer
            prepare = self._envelope(task, subtask, "task_prepare", request_id, execution_id or "", {
                "revision": subtask.revision, "chunk_count": len(encoded.chunks),
                "compressed_bytes": len(encoded.compressed), "raw_bytes": encoded.raw_bytes,
                "crc32": encoded.crc32, "compression": "zlib", "encoding": "json-utf8",
            })
            self._queue(prepare, subtask.ip_address, execution_id)
        self.transfer_updated.emit(task.task_id, subtask.device_id, "preparing")
        self._log(task.task_id, execution_id, "task_prepare", "开始下发子任务", subtask.device_id)

    def execute_devices(
        self,
        task: TaskDefinition,
        device_ids: tuple[str, ...],
        *,
        forced_conflict_reason: str | None = None,
    ) -> TaskExecutionSnapshot:
        selected = tuple(item for item in task.subtasks if item.device_id in device_ids)
        if not selected or len(selected) != len(set(device_ids)):
            raise ValueError("执行设备不属于任务")
        for item in selected:
            self._require_available_device(item.device_id)
            if item.device_id in self._device_execution:
                raise RuntimeError(f"设备 {item.device_id} 已有执行会话")
        execution_id = uuid.uuid4().hex
        snapshot = TaskExecutionSnapshot(
            execution_id, task.task_id, tuple(item.device_id for item in selected),
            TaskExecutionStatus.PREPARING, utc_now(), message="正在预下发任务",
            device_states=tuple((item.device_id, "preparing") for item in selected),
            forced_conflict_reason=forced_conflict_reason,
        )
        self.repository.create_execution(task.task_id, snapshot)
        self._executions[execution_id] = snapshot
        for item in selected:
            self._device_execution[item.device_id] = execution_id
            if item.is_delivered:
                self._set_device_state(execution_id, item.device_id, "ready")
            else:
                self.deliver_subtask(task, item, execution_id)
        self._maybe_schedule(execution_id)
        return self._executions[execution_id]

    def execute_subtask(self, task: TaskDefinition, device_id: str) -> TaskExecutionSnapshot:
        return self.execute_devices(task, (device_id,))

    def stop_execution(self, execution_id: str, reason: str = "用户终止任务") -> None:
        snapshot = self._executions.get(execution_id)
        if snapshot is None:
            return
        task = self.repository.task_by_id(snapshot.task_id)
        if task is None:
            return
        for subtask in task.subtasks:
            if subtask.device_id not in snapshot.device_ids:
                continue
            message_type = "cancel_execution" if snapshot.status in {TaskExecutionStatus.PREPARING, TaskExecutionStatus.SCHEDULED} else "stop_task"
            request_id = uuid.uuid4().hex
            envelope = self._envelope(task, subtask, message_type, request_id, execution_id, {"reason": reason})
            self._queue(envelope, subtask.ip_address, execution_id)
        self._update_execution(snapshot, TaskExecutionStatus.STOPPED, reason)
        self._release_execution(snapshot)

    def _run(self) -> None:
        while self._running.is_set():
            udp_socket = self._socket
            if udp_socket is None:
                break
            try:
                data, address = udp_socket.recvfrom(self.config.max_datagram_bytes + 1)
                self.process_datagram(data, address[0])
            except socket.timeout:
                pass
            except OSError:
                break
            self._tick()

    def process_datagram(self, datagram: bytes, peer_ip: str) -> None:
        try:
            envelope = self.protocol.decode(datagram)
        except TaskProtocolError as exc:
            self.protocol_warning.emit(str(exc))
            return
        device = self.device_lookup(envelope.device_id)
        if device is None or device.ip_address != peer_ip:
            self.protocol_warning.emit("任务消息来源设备或 IP 不匹配")
            return
        with self._lock:
            if envelope.message_type == "command_ack":
                self._handle_ack(envelope)
            elif envelope.message_type in {"task_status", "waypoint_progress", "task_heartbeat"}:
                self._handle_status(envelope)

    def _handle_ack(self, envelope: TaskEnvelope) -> None:
        pending = self._pending.pop(envelope.request_id, None)
        if pending is None:
            return
        accepted = envelope.payload["accepted"]
        if not accepted:
            reason = str(envelope.payload.get("reason", "端侧拒绝指令"))
            self.transfer_updated.emit(envelope.task_id, envelope.device_id, "failed")
            self._log(envelope.task_id, pending.execution_id, "command_rejected", reason, envelope.device_id, TaskEventLevel.ERROR)
            if pending.execution_id:
                self._fail_execution(pending.execution_id, reason)
            return
        command = str(envelope.payload.get("command", pending.envelope.message_type))
        if command == "task_prepare":
            transfer = self._transfers.get((envelope.task_id, envelope.device_id))
            if transfer:
                self._send_transfer_chunks(transfer)
        elif command == "task_commit":
            missing = envelope.payload.get("missing_chunks", [])
            if missing:
                transfer = self._transfers.get((envelope.task_id, envelope.device_id))
                if transfer is None:
                    return
                for index in missing:
                    if not isinstance(index, int) or not 0 <= index < len(transfer.encoded.chunks):
                        self._fail_execution(transfer.execution_id or "", "端侧返回非法缺失分片列表")
                        return
                    chunk = self._envelope(
                        transfer.task, transfer.subtask, "task_chunk", transfer.request_id,
                        transfer.execution_id or "", {
                            "revision": transfer.subtask.revision,
                            "chunk_count": len(transfer.encoded.chunks), "chunk_index": index,
                            "crc32": transfer.encoded.crc32, "data": transfer.encoded.chunks[index],
                        },
                    )
                    self._send(chunk, transfer.subtask.ip_address)
                commit_id = uuid.uuid4().hex
                self._queue(self._envelope(
                    transfer.task, transfer.subtask, "task_commit", commit_id,
                    transfer.execution_id or "", {
                        "revision": transfer.subtask.revision,
                        "chunk_count": len(transfer.encoded.chunks), "crc32": transfer.encoded.crc32,
                    },
                ), transfer.subtask.ip_address, transfer.execution_id)
                return
            transfer = self._transfers.pop((envelope.task_id, envelope.device_id), None)
            if transfer:
                try:
                    self.repository.mark_delivered(envelope.task_id, envelope.device_id, transfer.subtask.revision)
                except TaskRepositoryError as exc:
                    self.protocol_warning.emit(str(exc))
                self.transfer_updated.emit(envelope.task_id, envelope.device_id, "delivered")
                self._log(envelope.task_id, transfer.execution_id, "task_delivered", "子任务下发完成", envelope.device_id)
                if transfer.execution_id:
                    self._set_device_state(transfer.execution_id, envelope.device_id, "ready")
                    self._maybe_schedule(transfer.execution_id)
        elif command == "execute_task" and envelope.execution_id:
            self._set_device_state(envelope.execution_id, envelope.device_id, "scheduled")
            self._maybe_mark_scheduled(envelope.execution_id)

    def _send_transfer_chunks(self, transfer: _Transfer) -> None:
        for index, chunk in enumerate(transfer.encoded.chunks):
            envelope = self._envelope(
                transfer.task, transfer.subtask, "task_chunk", transfer.request_id,
                transfer.execution_id or "", {
                    "revision": transfer.subtask.revision, "chunk_count": len(transfer.encoded.chunks),
                    "chunk_index": index, "crc32": transfer.encoded.crc32, "data": chunk,
                },
            )
            self._send(envelope, transfer.subtask.ip_address)
        commit_id = uuid.uuid4().hex
        commit = self._envelope(transfer.task, transfer.subtask, "task_commit", commit_id, transfer.execution_id or "", {
            "revision": transfer.subtask.revision, "chunk_count": len(transfer.encoded.chunks),
            "crc32": transfer.encoded.crc32,
        })
        self._queue(commit, transfer.subtask.ip_address, transfer.execution_id)
        self.transfer_updated.emit(transfer.task.task_id, transfer.subtask.device_id, "committing")

    def _maybe_schedule(self, execution_id: str) -> None:
        snapshot = self._executions.get(execution_id)
        if snapshot is None or not all(state == "ready" for _, state in snapshot.device_states):
            return
        task = self.repository.task_by_id(snapshot.task_id)
        if task is None:
            return
        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=self.config.group_start_delay_seconds)
        snapshot = replace(snapshot, scheduled_at=scheduled_at, message="等待设备确认统一启动时间", updated_at=utc_now())
        self._executions[execution_id] = snapshot
        self.repository.update_execution(snapshot)
        for subtask in task.subtasks:
            if subtask.device_id not in snapshot.device_ids:
                continue
            request_id = uuid.uuid4().hex
            command = self._envelope(task, subtask, "execute_task", request_id, execution_id, {
                "revision": subtask.revision, "scheduled_at": scheduled_at.isoformat(),
            })
            self._queue(command, subtask.ip_address, execution_id)

    def _maybe_mark_scheduled(self, execution_id: str) -> None:
        snapshot = self._executions.get(execution_id)
        if snapshot and all(state == "scheduled" for _, state in snapshot.device_states):
            now = self.clock()
            for device_id in snapshot.device_ids:
                self._last_heartbeat[(execution_id, device_id)] = now
            self._update_execution(snapshot, TaskExecutionStatus.SCHEDULED, "所有设备已确认统一启动")

    def _handle_status(self, envelope: TaskEnvelope) -> None:
        self.event_received.emit(envelope.device_id, envelope)
        execution_id = envelope.execution_id
        if not execution_id or execution_id not in self._executions:
            return
        state = str(envelope.payload.get("state", ""))
        self._last_heartbeat[(execution_id, envelope.device_id)] = self.clock()
        self._log(envelope.task_id, execution_id, envelope.message_type, str(envelope.payload.get("message", state)), envelope.device_id)
        if envelope.message_type == "task_status" and state:
            self._set_device_state(execution_id, envelope.device_id, state)
            snapshot = self._executions[execution_id]
            states = dict(snapshot.device_states)
            if any(value == "failed" for value in states.values()):
                self._fail_execution(execution_id, "设备报告任务失败")
            elif all(value == "completed" for value in states.values()):
                self._update_execution(snapshot, TaskExecutionStatus.COMPLETED, "任务执行完成")
                self._release_execution(snapshot)
            elif any(value == "running" for value in states.values()):
                self._update_execution(snapshot, TaskExecutionStatus.RUNNING, "任务执行中")

    def _tick(self) -> None:
        now = self.clock()
        with self._lock:
            for request_id, pending in list(self._pending.items()):
                if now - pending.last_sent < self.config.retry_seconds:
                    continue
                if pending.attempts >= self.config.max_attempts:
                    self._pending.pop(request_id, None)
                    reason = f"设备 {pending.device_id} 未确认 {pending.envelope.message_type}"
                    self._log(pending.task_id, pending.execution_id, "command_timeout", reason, pending.device_id, TaskEventLevel.ERROR)
                    if pending.execution_id:
                        self._fail_execution(pending.execution_id, reason)
                    continue
                self._send_pending(pending)
            for execution_id, snapshot in list(self._executions.items()):
                if snapshot.status not in {TaskExecutionStatus.SCHEDULED, TaskExecutionStatus.RUNNING}:
                    continue
                for device_id, state in snapshot.device_states:
                    last = self._last_heartbeat.get((execution_id, device_id))
                    if last is not None and now - last > self.config.heartbeat_timeout_seconds:
                        self._fail_execution(execution_id, f"设备 {device_id} 任务心跳超时")
                        break

    def _queue(self, envelope: TaskEnvelope, peer_ip: str, execution_id: str | None) -> None:
        pending = _PendingCommand(envelope, peer_ip, envelope.task_id, execution_id, envelope.device_id)
        self._pending[envelope.request_id] = pending
        self._send_pending(pending)

    def _send_pending(self, pending: _PendingCommand) -> None:
        self._send(pending.envelope, pending.peer_ip)
        pending.attempts += 1
        pending.last_sent = self.clock()

    def _send(self, envelope: TaskEnvelope, peer_ip: str) -> None:
        if not self.available or self._socket is None:
            raise RuntimeError(self.module_message)
        self._socket.sendto(self.protocol.encode(envelope), (peer_ip, self.config.device_control_port))

    def _envelope(
        self, task: TaskDefinition, subtask: DeviceSubtask, message_type: str,
        request_id: str, execution_id: str, payload: dict[str, object],
    ) -> TaskEnvelope:
        self._sequence += 1
        return TaskEnvelope(
            task.task_id, subtask.subtask_id, subtask.device_id, execution_id,
            message_type, request_id, self._sequence, time.time_ns(), payload,
        )

    def _set_device_state(self, execution_id: str, device_id: str, state: str) -> None:
        snapshot = self._executions.get(execution_id)
        if snapshot is None:
            return
        states = dict(snapshot.device_states)
        states[device_id] = state
        updated = replace(snapshot, device_states=tuple(states.items()), updated_at=utc_now())
        self._executions[execution_id] = updated
        self.repository.update_execution(updated)
        self.execution_updated.emit(updated)

    def _update_execution(self, snapshot: TaskExecutionSnapshot, status: TaskExecutionStatus, message: str) -> None:
        updated = replace(snapshot, status=status, message=message, updated_at=utc_now())
        self._executions[snapshot.execution_id] = updated
        self.repository.update_execution(updated)
        self.execution_updated.emit(updated)
        self._log(snapshot.task_id, snapshot.execution_id, "execution_status", message)

    def _fail_execution(self, execution_id: str, reason: str) -> None:
        snapshot = self._executions.get(execution_id)
        if snapshot is None or snapshot.status in {TaskExecutionStatus.FAILED, TaskExecutionStatus.COMPLETED}:
            return
        self._update_execution(snapshot, TaskExecutionStatus.FAILED, reason)
        self._release_execution(snapshot)

    def _release_execution(self, snapshot: TaskExecutionSnapshot) -> None:
        for device_id in snapshot.device_ids:
            if self._device_execution.get(device_id) == snapshot.execution_id:
                self._device_execution.pop(device_id, None)
            self._last_heartbeat.pop((snapshot.execution_id, device_id), None)
        self._executions.pop(snapshot.execution_id, None)

    def _require_available_device(self, device_id: str) -> DeviceSnapshot:
        if not self.available:
            raise RuntimeError(self.module_message)
        device = self.device_lookup(device_id)
        if device is None or not device.ip_address:
            raise ValueError(f"设备 {device_id} 不存在或缺少 IP")
        return device

    def _log(
        self, task_id: str, execution_id: str | None, event_type: str, message: str,
        device_id: str | None = None, level: TaskEventLevel = TaskEventLevel.INFO,
    ) -> None:
        if execution_id:
            self.repository.append_execution_event(
                task_id, execution_id, event_type, message, level=level, device_id=device_id
            )
        else:
            self.repository.append_audit(task_id, event_type, message, level=level, device_id=device_id)
