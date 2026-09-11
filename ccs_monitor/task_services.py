from __future__ import annotations

import socket
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from functools import wraps
from datetime import datetime, timedelta, timezone
from typing import Callable

from PySide6.QtCore import QObject, Signal

from .device_address import device_address_matches
from .models import ConnectionStatus, DeviceSnapshot, utc_now
from .task_config import TaskSystemConfig
from .task_models import (
    DeviceSubtask, EdgeTaskStatus, TaskDefinition, TaskEventLevel, TaskExecutionSnapshot, TaskExecutionStatus,
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
    commit_id: str = ""
    repair_rounds: int = 0


@dataclass
class _Confirmation:
    subtask_id: str
    revision: int
    request_ids: set[str] = field(default_factory=set)
    deadline: float | None = None
    sequence: int = -1
    transfer: bool = False
    last_query: float = 0.0


def _synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


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
        active_map_id_getter: Callable[[], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        socket_factory: Callable[..., socket.socket] = socket.socket,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.repository = repository
        self.device_lookup = device_lookup
        self.active_map_id_getter = active_map_id_getter
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
        self._confirmations: dict[tuple[str, str], _Confirmation] = {}

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

    @_synchronized
    def deliver_subtask(self, task: TaskDefinition, subtask: DeviceSubtask, execution_id: str | None = None) -> None:
        self._require_online_device(subtask.device_id)
        saved = self.repository.task_by_id(task.task_id)
        current = next((item for item in saved.subtasks if item.subtask_id == subtask.subtask_id), None) if saved else None
        if current is None or current.revision != subtask.revision or not current.same_definition(subtask):
            raise ValueError("子任务已改变，请先保存当前版本")
        if self.device_active(subtask.device_id):
            raise RuntimeError("设备已有执行会话")
        if not subtask.is_valid or subtask.revision <= 0:
            raise ValueError("子任务尚未保存有效轨迹")
        encoded = self.protocol.encode_subtask(task, subtask)
        request_id = uuid.uuid4().hex
        transfer = _Transfer(task, subtask, encoded, request_id, execution_id)
        with self._lock:
            self._clear_transfer(task.task_id, subtask.device_id)
            self.repository.invalidate_delivery(task.task_id, subtask.device_id, receiving=True)
            self._confirmations[(task.task_id, subtask.device_id)] = _Confirmation(subtask.subtask_id, subtask.revision, transfer=True)
            self._transfers[(task.task_id, subtask.device_id)] = transfer
            prepare = self._envelope(task, subtask, "task_prepare", request_id, execution_id or "", {
                "revision": subtask.revision, "chunk_count": len(encoded.chunks),
                "compressed_bytes": len(encoded.compressed), "raw_bytes": encoded.raw_bytes,
                "crc32": encoded.crc32, "compression": "zlib", "encoding": "json-utf8",
            })
            try:
                self._queue(prepare, subtask.ip_address, execution_id)
            except Exception as exc:
                self._fail_transfer(task.task_id, subtask.device_id, str(exc))
                raise
        self.transfer_updated.emit(task.task_id, subtask.device_id, "preparing")
        self._log(task.task_id, execution_id, "task_prepare", "开始下发子任务", subtask.device_id)

    @_synchronized
    def deliver_task(self, task: TaskDefinition) -> None:
        """Validate the complete batch before sending any command."""
        current = self.repository.task_by_id(task.task_id)
        if current is None or not current.is_ready:
            raise ValueError("每台设备必须具有有效子任务")
        for item in current.subtasks:
            self._require_online_device(item.device_id)
            if self.device_active(item.device_id) or self.transfer_active(current.task_id, item.device_id):
                raise RuntimeError(f"设备 {item.device_id} 正在下发或执行")
            self.protocol.encode_subtask(current, item)
        for item in current.subtasks:
            try:
                self.deliver_subtask(current, item)
            except Exception as exc:
                self._fail_transfer(current.task_id, item.device_id, str(exc))

    def transfer_active(self, task_id: str, device_id: str) -> bool:
        with self._lock:
            key = (task_id, device_id)
            confirmation = self._confirmations.get(key)
            return key in self._transfers or bool(confirmation and confirmation.transfer and confirmation.deadline is not None)

    @_synchronized
    def negotiate_subtask(self, task: TaskDefinition, subtask: DeviceSubtask) -> None:
        if self.transfer_active(task.task_id, subtask.device_id) or self.device_active(subtask.device_id):
            return
        self.repository.invalidate_delivery(task.task_id, subtask.device_id)
        self._require_online_device(subtask.device_id)
        self._clear_transfer(task.task_id, subtask.device_id)
        request_id = uuid.uuid4().hex
        self._confirmations[(task.task_id, subtask.device_id)] = _Confirmation(
            subtask.subtask_id, subtask.revision, {request_id},
            self.clock() + self.config.navigation_ready_timeout_seconds, last_query=self.clock())
        self._queue(self._envelope(task, subtask, "negotiate_task", request_id, "", {"revision": subtask.revision}), subtask.ip_address, None)

    @_synchronized
    def read_subtask(self, task: TaskDefinition, subtask: DeviceSubtask) -> None:
        if self.transfer_active(task.task_id, subtask.device_id):
            raise RuntimeError("设备正在下发，请等待本轮完成")
        self._require_available_device(subtask.device_id)
        request_id = uuid.uuid4().hex
        self._queue(self._envelope(task, subtask, "read_task", request_id, "", {}), subtask.ip_address, None)

    @_synchronized
    def delete_subtask(self, task: TaskDefinition, subtask: DeviceSubtask) -> None:
        self._require_available_device(subtask.device_id)
        self._queue(self._envelope(task, subtask, "delete_task", uuid.uuid4().hex, "", {}), subtask.ip_address, None)

    def emergency_stop(self, task: TaskDefinition) -> None:
        for subtask in task.subtasks:
            if subtask.ip_address:
                self._queue(self._envelope(task, subtask, "emergency_stop", uuid.uuid4().hex, "", {"reason": "指控平台急停"}), subtask.ip_address, None)

    @_synchronized
    def execute_devices(
        self,
        task: TaskDefinition,
        device_ids: tuple[str, ...],
        *,
        forced_conflict_reason: str | None = None,
    ) -> TaskExecutionSnapshot:
        if self.active_map_id_getter is not None and self.active_map_id_getter() != task.map_id:
            raise RuntimeError("任务绑定地图不是当前全局激活地图")
        task = self.repository.task_by_id(task.task_id) or task
        selected = tuple(item for item in task.subtasks if item.device_id in device_ids)
        if not selected or len(selected) != len(set(device_ids)):
            raise ValueError("执行设备不属于任务")
        for item in selected:
            self._require_online_device(item.device_id)
            if self.transfer_active(task.task_id, item.device_id):
                raise RuntimeError(f"设备 {item.device_id} 尚在等待下发确认")
            if item.device_id in self._device_execution:
                raise RuntimeError(f"设备 {item.device_id} 已有执行会话")
            if not item.edge_ready:
                if not item.is_delivered:
                    reason = "任务尚未保存下发当前 revision"
                elif item.edge_status == EdgeTaskStatus.FAILED:
                    reason = item.edge_message or "端侧导航准备失败"
                else:
                    reason = "端侧导航尚未准备完成"
                raise RuntimeError(f"设备 {item.device_id} 不可执行：{reason}")
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
            self._set_device_state(execution_id, item.device_id, "ready")
        self._maybe_schedule(execution_id)
        return self._executions[execution_id]

    def execute_subtask(self, task: TaskDefinition, device_id: str) -> TaskExecutionSnapshot:
        return self.execute_devices(task, (device_id,))

    @_synchronized
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
            message_type = "terminate_task"
            request_id = uuid.uuid4().hex
            envelope = self._envelope(task, subtask, message_type, request_id, execution_id, {"reason": reason})
            self._queue(envelope, subtask.ip_address, execution_id)
        for device_id in snapshot.device_ids:
            self._set_device_state(execution_id, device_id, "stopping")
            self._last_heartbeat[(execution_id, device_id)] = self.clock()
        current = self._executions.get(execution_id)
        if current is not None:
            self._update_execution(current, TaskExecutionStatus.STOPPING, reason)

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
        if device is None or not device_address_matches(device.ip_address, peer_ip):
            self.protocol_warning.emit("任务消息来源设备或地址不匹配")
            return
        with self._lock:
            try:
                if envelope.message_type == "command_ack":
                    self._handle_ack(envelope)
                elif envelope.message_type == "task_summary":
                    self._handle_summary(envelope)
                elif envelope.message_type in {"task_status", "waypoint_progress", "task_heartbeat"}:
                    self._handle_status(envelope)
            except (OSError, RuntimeError) as exc:
                self.protocol_warning.emit(f"处理任务回执失败：{exc}")
                try:
                    self._fail_transfer(envelope.task_id, envelope.device_id, str(exc))
                except (OSError, TaskRepositoryError):
                    pass  # A storage failure must not terminate the receiver.

    def _handle_summary(self, envelope: TaskEnvelope) -> None:
        task = self.repository.task_by_id(envelope.task_id)
        if task is None:
            return
        current = next((item for item in task.subtasks if item.device_id.casefold() == envelope.device_id.casefold()), None)
        context = self._confirmations.get((envelope.task_id, envelope.device_id))
        if (current is None or current.subtask_id != envelope.subtask_id or context is None
                or context.subtask_id != envelope.subtask_id or context.revision != current.revision
                or envelope.request_id not in context.request_ids or envelope.sequence <= context.sequence):
            return
        revision = envelope.payload.get("revision")
        if revision is not None and revision != current.revision:
            return
        context.sequence = envelope.sequence
        state = str(envelope.payload.get("state", "no_task"))
        try:
            edge_status = EdgeTaskStatus(state)
        except ValueError:
            edge_status = EdgeTaskStatus.FAILED
        message = str(envelope.payload.get("message", ""))
        error_code = str(envelope.payload.get("error_code") or "")
        if error_code:
            message = f"{message} ({error_code})" if message else error_code
        updated = replace(current, edge_status=edge_status,
                          edge_revision=int(envelope.payload["revision"]) if envelope.payload.get("revision") is not None else None,
                          edge_message=message, edge_updated_at=utc_now())
        try:
            self.repository.update_edge_status(task.task_id, updated)
        except TaskRepositoryError:
            return
        if edge_status == EdgeTaskStatus.FAILED:
            self._fail_transfer(task.task_id, current.device_id, message or "导航准备失败")
        elif updated.edge_ready:
            context.deadline = None
            self.transfer_updated.emit(task.task_id, current.device_id, "ready")
        elif edge_status in {EdgeTaskStatus.NO_TASK, EdgeTaskStatus.COMPLETED, EdgeTaskStatus.EMERGENCY_STOP}:
            context.deadline = None
        execution_id = self._device_execution.get(envelope.device_id)
        snapshot = self._executions.get(execution_id or "")
        if snapshot is not None and snapshot.task_id == task.task_id and updated.edge_ready:
            self._set_device_state(snapshot.execution_id, envelope.device_id, "ready")
            self._maybe_schedule(snapshot.execution_id)
        level = TaskEventLevel.ERROR if edge_status == EdgeTaskStatus.FAILED else TaskEventLevel.INFO
        self._log(
            task.task_id, execution_id, "task_summary",
            f"{state}: {message}" if message else state,
            envelope.device_id, level,
        )
        self.event_received.emit(envelope.device_id, envelope)

    def _handle_ack(self, envelope: TaskEnvelope) -> None:
        pending = self._pending.get(envelope.request_id)
        if pending is None:
            return
        expected = pending.envelope
        if (envelope.task_id, envelope.subtask_id, envelope.device_id, envelope.execution_id) != (
                expected.task_id, expected.subtask_id, expected.device_id, expected.execution_id):
            return
        if envelope.payload.get("command", expected.message_type) != expected.message_type:
            return
        key = (envelope.task_id, envelope.device_id)
        transfer = self._transfers.get(key)
        if expected.message_type in {"task_prepare", "task_commit"}:
            if transfer is None or envelope.request_id != (transfer.request_id if expected.message_type == "task_prepare" else transfer.commit_id):
                return
        context = self._confirmations.get(key)
        if context is not None:
            task = self.repository.task_by_id(envelope.task_id)
            current = next((item for item in task.subtasks if item.subtask_id == context.subtask_id), None) if task else None
            if current is None or current.revision != context.revision:
                self._clear_transfer(*key)
                return
        self._pending.pop(envelope.request_id, None)
        accepted = envelope.payload["accepted"]
        if not accepted:
            reason = str(envelope.payload.get("reason", "端侧拒绝指令"))
            self._fail_transfer(envelope.task_id, envelope.device_id, reason)
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
                transfer.repair_rounds += 1
                if (not isinstance(missing, list) or transfer.repair_rounds > self.config.max_attempts
                        or any(type(index) is not int or not 0 <= index < len(transfer.encoded.chunks) for index in missing)):
                    self._fail_transfer(envelope.task_id, envelope.device_id, "缺失分片列表非法或重传次数超限")
                    return
                for index in missing:
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
                transfer.commit_id = commit_id
                context = self._confirmations[key]
                context.request_ids = {commit_id}
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
                    self._fail_transfer(envelope.task_id, envelope.device_id, str(exc))
                    return
                context = self._confirmations.get(key)
                if context is not None:
                    context.deadline = self.clock() + self.config.navigation_ready_timeout_seconds
                self.transfer_updated.emit(envelope.task_id, envelope.device_id, "delivered")
                self._log(envelope.task_id, transfer.execution_id, "task_delivered", "子任务下发完成", envelope.device_id)
                refreshed = self.repository.task_by_id(envelope.task_id)
                refreshed_subtask = next((item for item in refreshed.subtasks
                                          if item.device_id == envelope.device_id), None) if refreshed else None
                if refreshed_subtask is not None and refreshed_subtask.edge_ready:
                    if context is not None:
                        context.deadline = None
                    self.transfer_updated.emit(envelope.task_id, envelope.device_id, "ready")
                if transfer.execution_id and refreshed_subtask is not None and refreshed_subtask.edge_ready:
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
        transfer.commit_id = commit_id
        self._confirmations[(transfer.task.task_id, transfer.subtask.device_id)].request_ids = {commit_id}
        commit = self._envelope(transfer.task, transfer.subtask, "task_commit", commit_id, transfer.execution_id or "", {
            "revision": transfer.subtask.revision, "chunk_count": len(transfer.encoded.chunks),
            "crc32": transfer.encoded.crc32,
        })
        self._queue(commit, transfer.subtask.ip_address, transfer.execution_id)
        self.transfer_updated.emit(transfer.task.task_id, transfer.subtask.device_id, "committing")

    def _maybe_schedule(self, execution_id: str) -> None:
        snapshot = self._executions.get(execution_id)
        if snapshot is None or snapshot.scheduled_at is not None or not all(state == "ready" for _, state in snapshot.device_states):
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
        snapshot = self._executions[execution_id]
        task = self.repository.task_by_id(snapshot.task_id)
        if (envelope.task_id != snapshot.task_id or envelope.device_id not in snapshot.device_ids
                or task is None or not any(item.device_id == envelope.device_id and item.subtask_id == envelope.subtask_id for item in task.subtasks)):
            return
        state = str(envelope.payload.get("state", ""))
        self._last_heartbeat[(execution_id, envelope.device_id)] = self.clock()
        if envelope.message_type != "task_heartbeat" and (envelope.message_type != "task_status" or dict(snapshot.device_states).get(envelope.device_id) != state):
            self._log(envelope.task_id, execution_id, envelope.message_type, str(envelope.payload.get("message", state)), envelope.device_id)
        if envelope.message_type == "task_status" and state:
            self._set_device_state(execution_id, envelope.device_id, state)
            snapshot = self._executions[execution_id]
            states = dict(snapshot.device_states)
            if any(value == "failed" for value in states.values()):
                message = str(envelope.payload.get("message", "设备报告任务失败"))
                error_code = str(envelope.payload.get("error_code") or "")
                detail = f"设备 {envelope.device_id} 任务失败：{message}"
                if error_code:
                    detail += f"（{error_code}）"
                self._fail_execution(execution_id, detail)
            elif snapshot.status == TaskExecutionStatus.STOPPING:
                if all(value in {"stopped", "completed"} for value in states.values()):
                    self._update_execution(snapshot, TaskExecutionStatus.STOPPED, "所有设备已停止")
                    self._release_execution(snapshot)
            elif all(value == "completed" for value in states.values()):
                self._update_execution(snapshot, TaskExecutionStatus.COMPLETED, "任务执行完成")
                self._release_execution(snapshot)
            elif any(value == "running" for value in states.values()):
                self._update_execution(snapshot, TaskExecutionStatus.RUNNING, "任务执行中")

    def _tick(self) -> None:
        now = self.clock()
        with self._lock:
            for key, context in list(self._confirmations.items()):
                task = self.repository.task_by_id(key[0])
                current = next((item for item in task.subtasks if item.subtask_id == context.subtask_id), None) if task else None
                if current is None or current.revision != context.revision:
                    self._clear_transfer(*key)
                    continue
                device = self.device_lookup(key[1])
                if device is None or device.connection_status != ConnectionStatus.ONLINE:
                    self._fail_transfer(*key, "设备连接中断")
                elif context.deadline is not None and now >= context.deadline:
                    self._fail_transfer(*key, "等待导航就绪超时")
                elif (not context.transfer and context.deadline is not None and now - context.last_query >= 2
                      and not any(request in self._pending for request in context.request_ids)):
                    # A preparation begun before reconnect uses an old commit ID.
                    # Ask its current state with a fresh request; never trust that old ID.
                    request_id = uuid.uuid4().hex
                    context.request_ids = {request_id}
                    context.last_query = now
                    try:
                        self._queue(self._envelope(task, current, "negotiate_task", request_id, "",
                                                  {"revision": current.revision}), current.ip_address, None)
                    except (OSError, RuntimeError) as exc:
                        self._fail_transfer(*key, str(exc))
            for request_id, pending in list(self._pending.items()):
                if request_id not in self._pending:
                    continue
                if now - pending.last_sent < self.config.retry_seconds:
                    continue
                if pending.attempts >= self.config.max_attempts:
                    self._pending.pop(request_id, None)
                    reason = f"设备 {pending.device_id} 未确认 {pending.envelope.message_type}"
                    self._log(pending.task_id, pending.execution_id, "command_timeout", reason, pending.device_id, TaskEventLevel.ERROR)
                    if pending.envelope.message_type in {"task_prepare", "task_commit", "negotiate_task", "read_task"}:
                        self._fail_transfer(pending.task_id, pending.device_id, reason)
                    if pending.execution_id:
                        self._fail_execution(pending.execution_id, reason)
                    continue
                try:
                    self._send_pending(pending)
                except (OSError, RuntimeError) as exc:
                    self._fail_transfer(pending.task_id, pending.device_id, str(exc))
            for execution_id, snapshot in list(self._executions.items()):
                if snapshot.status not in {
                    TaskExecutionStatus.SCHEDULED, TaskExecutionStatus.RUNNING,
                    TaskExecutionStatus.STOPPING,
                }:
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
        if device_id not in states or states[device_id] == state:
            return
        states[device_id] = state
        updated = replace(snapshot, device_states=tuple(states.items()), updated_at=utc_now())
        self._executions[execution_id] = updated
        self.repository.update_execution(updated)
        self.execution_updated.emit(updated)

    def _update_execution(self, snapshot: TaskExecutionSnapshot, status: TaskExecutionStatus, message: str) -> None:
        if snapshot.status == status and snapshot.message == message:
            return
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

    def _clear_transfer(self, task_id: str, device_id: str) -> None:
        self._transfers.pop((task_id, device_id), None)
        self._confirmations.pop((task_id, device_id), None)
        for request_id, pending in tuple(self._pending.items()):
            if pending.task_id == task_id and pending.device_id == device_id and pending.envelope.message_type in {
                    "task_prepare", "task_commit", "negotiate_task", "read_task"}:
                self._pending.pop(request_id, None)

    def _fail_transfer(self, task_id: str, device_id: str, reason: str) -> None:
        self._clear_transfer(task_id, device_id)
        task = self.repository.task_by_id(task_id)
        current = next((item for item in task.subtasks if item.device_id == device_id), None) if task else None
        if current is not None:
            try:
                self.repository.update_edge_status(task_id, replace(current, edge_status=EdgeTaskStatus.FAILED,
                    edge_revision=None, edge_message=reason, edge_updated_at=utc_now()))
            except TaskRepositoryError as exc:
                self.protocol_warning.emit(str(exc))
        self.transfer_updated.emit(task_id, device_id, "failed")

    def _require_online_device(self, device_id: str) -> DeviceSnapshot:
        device = self._require_available_device(device_id)
        if device.connection_status != ConnectionStatus.ONLINE:
            raise RuntimeError(f"设备 {device_id} 不在线")
        return device

    def _require_available_device(self, device_id: str) -> DeviceSnapshot:
        if not self.available:
            raise RuntimeError(self.module_message)
        device = self.device_lookup(device_id)
        if device is None or not device.ip_address:
            raise ValueError(f"设备 {device_id} 不存在或缺少有效地址")
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
