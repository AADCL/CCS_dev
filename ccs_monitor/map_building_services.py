from __future__ import annotations

import socket
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from PySide6.QtCore import QObject, Signal

from .map_building import (
    CloudFrameAssembler,
    MapBuildingEnvelope,
    MapBuildingProtocol,
    MapBuildingProtocolError,
    MapBuildingSessionSnapshot,
    VoxelMapAccumulator,
)
from .map_building_config import MapBuildingConfig
from .map_repository import MapRepository, MapRepositoryError
from .models import DeviceSnapshot, MapBuildingResultMetadata, MapDefinition


@dataclass
class _ActiveSession:
    definition: MapDefinition
    device: DeviceSnapshot
    session_id: str
    request_id: str
    started_at: datetime
    started_monotonic: float
    assembler: CloudFrameAssembler
    accumulator: VoxelMapAccumulator
    state: str = "negotiating"
    message: str = "正在与端侧协商"
    command: str = "start_mapping"
    command_attempts: int = 0
    last_command_at: float = 0.0
    last_complete_frame_at: float | None = None
    last_data_at: datetime | None = None
    complete_frames: int = 0
    dropped_frames: int = 0
    last_sequence: int = -1
    seen_cloud_sequences: set[int] | None = None
    trajectory: list[tuple[object, ...]] | None = None
    last_preview_at: float = 0.0
    last_checkpoint_at: float = 0.0

    def __post_init__(self) -> None:
        self.seen_cloud_sequences = set()
        self.trajectory = []


class MapBuildingService(QObject):
    session_updated = Signal(object)
    preview_updated = Signal(str, object, object)
    completed = Signal(object)
    failed = Signal(str)
    availability_changed = Signal(bool, str)

    def __init__(
        self,
        config: MapBuildingConfig,
        repository: MapRepository,
        *,
        clock: Callable[[], float] = time.monotonic,
        socket_factory: Callable[..., socket.socket] = socket.socket,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.repository = repository
        self.clock = clock
        self.socket_factory = socket_factory
        self.protocol = MapBuildingProtocol(config)
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.RLock()
        self._session: _ActiveSession | None = None
        self.available = False
        self.module_message = "UDP 建图模块尚未启动"

    @property
    def active(self) -> bool:
        with self._lock:
            return self._session is not None

    @property
    def current_snapshot(self) -> MapBuildingSessionSnapshot | None:
        with self._lock:
            return self._snapshot(self._session) if self._session else None

    def start(self) -> None:
        if self._running.is_set():
            return
        udp_socket = None
        try:
            udp_socket = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.bind((self.config.bind_host, self.config.data_port))
            udp_socket.settimeout(0.1)
        except OSError as exc:
            if udp_socket is not None:
                udp_socket.close()
            self.available = False
            self.module_message = f"UDP {self.config.data_port} 端口绑定失败：{exc}"
            self.availability_changed.emit(False, self.module_message)
            return
        self._socket = udp_socket
        self._running.set()
        self.available = True
        self.module_message = f"UDP 建图监听 {self.config.bind_host}:{self.config.data_port}"
        self.availability_changed.emit(True, self.module_message)
        self._thread = threading.Thread(target=self._run, name="ccs-map-building", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.interrupt_mapping("应用退出")
        self._running.clear()
        udp_socket = self._socket
        self._socket = None
        if udp_socket is not None:
            try:
                udp_socket.close()
            except OSError:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self.available = False

    def start_mapping(self, definition: MapDefinition, device: DeviceSnapshot) -> str:
        if not self.available or self._socket is None:
            raise RuntimeError(self.module_message)
        if not device.ip_address:
            raise ValueError("设备缺少有效 IP 地址")
        with self._lock:
            if self._session is not None:
                raise RuntimeError("已有建图会话正在运行")
            now = datetime.now(timezone.utc)
            session_id = uuid.uuid4().hex
            session = _ActiveSession(
                definition=definition,
                device=device,
                session_id=session_id,
                request_id=uuid.uuid4().hex,
                started_at=now,
                started_monotonic=self.clock(),
                assembler=CloudFrameAssembler(self.config, self.clock),
                accumulator=VoxelMapAccumulator(
                    self.config.voxel_size_m,
                    self.config.max_accumulated_voxels,
                    self.config.max_preview_points,
                ),
            )
            session.last_checkpoint_at = session.started_monotonic
            self._session = session
            try:
                self._send_command(session, force=True)
            except OSError:
                self._session = None
                raise
            self._emit_snapshot(session)
            return session_id

    def stop_mapping(self, reason: str = "用户结束建图") -> None:
        with self._lock:
            session = self._session
            if session is None:
                return
            session.state = "saving"
            session.message = "正在结束端侧会话并保存"
            session.command = "stop_mapping"
            session.request_id = uuid.uuid4().hex
            session.command_attempts = 0
            self._send_command(session, force=True, reason=reason)
            self._emit_snapshot(session)

    def interrupt_mapping(self, reason: str = "建图会话中断") -> None:
        with self._lock:
            session = self._session
            if session is None:
                return
            try:
                self._send_stop_once(session, reason)
                self._checkpoint(session, state="interrupted", message=reason)
            except Exception as exc:
                self.failed.emit(f"保留临时建图结果失败：{exc}")
            session.state = "interrupted"
            session.message = reason
            self._emit_snapshot(session)
            self._session = None

    def save_interrupted_session(self, map_id: str, session_id: str) -> MapDefinition:
        sessions = self.repository.interrupted_sessions(map_id)
        payload = next((item for item in sessions if item.get("session_id") == session_id), None)
        if payload is None:
            raise MapRepositoryError("临时建图会话不存在")
        metadata = self._metadata_from_payload(payload)
        return self.repository.commit_mapping_result(map_id, session_id, metadata)

    def _run(self) -> None:
        while self._running.is_set():
            udp_socket = self._socket
            if udp_socket is None:
                break
            try:
                datagram, address = udp_socket.recvfrom(self.config.max_datagram_bytes + 1)
                self._handle_datagram(datagram, address[0])
            except socket.timeout:
                pass
            except OSError:
                if self._running.is_set():
                    self.failed.emit("UDP 建图接收 socket 异常")
                break
            except Exception as exc:
                self.failed.emit(f"UDP 建图数据处理失败：{exc}")
            self._tick()

    def _handle_datagram(self, datagram: bytes, peer_ip: str) -> None:
        envelope = self.protocol.decode(datagram)
        with self._lock:
            session = self._session
            if session is None:
                return
            if peer_ip != session.device.ip_address:
                raise MapBuildingProtocolError("数据报来源 IP 与设备配置不一致")
            if (
                envelope.map_id != session.definition.map_id
                or envelope.device_id.casefold() != session.device.device_id.casefold()
                or envelope.session_id != session.session_id
            ):
                raise MapBuildingProtocolError("数据报地图、设备或会话标识不一致")
            if envelope.message_type == "cloud_chunk":
                assert session.seen_cloud_sequences is not None
                if envelope.sequence in session.seen_cloud_sequences:
                    return
                session.seen_cloud_sequences.add(envelope.sequence)
                if len(session.seen_cloud_sequences) > 100000:
                    floor = max(session.seen_cloud_sequences) - 50000
                    session.seen_cloud_sequences = {item for item in session.seen_cloud_sequences if item >= floor}
                self._handle_cloud(session, envelope)
                return
            if envelope.sequence <= session.last_sequence:
                return
            session.last_sequence = envelope.sequence
            if envelope.message_type == "command_ack":
                self._handle_ack(session, envelope)
            elif envelope.message_type == "session_heartbeat":
                if session.state not in {"saving", "completed"}:
                    session.state = "mapping"
                    session.message = "端侧建图会话在线"
                    self._emit_snapshot(session)
            elif envelope.message_type == "session_status":
                state = envelope.payload["state"]
                if state == "error":
                    self._interrupt_locked(session, envelope.payload.get("reason") or "端侧传感器错误")
                elif state == "stopped" and session.state == "saving":
                    self._finalize(session)

    def _handle_ack(self, session: _ActiveSession, envelope: MapBuildingEnvelope) -> None:
        payload = envelope.payload
        if payload["request_id"] != session.request_id or payload["command"] != session.command:
            return
        if not payload["accepted"]:
            self._interrupt_locked(session, payload.get("reason") or "端侧拒绝建图指令")
            return
        if session.command == "start_mapping":
            session.state = "mapping"
            session.message = "建图中"
            self._emit_snapshot(session)
        else:
            self._finalize(session)

    def _handle_cloud(self, session: _ActiveSession, envelope: MapBuildingEnvelope) -> None:
        completed = session.assembler.push(envelope)
        if completed is None:
            return
        points, trajectory = completed
        session.accumulator.add(points)
        assert session.trajectory is not None
        session.trajectory.append(trajectory)
        session.complete_frames += 1
        now = self.clock()
        session.last_complete_frame_at = now
        session.last_data_at = datetime.now(timezone.utc)
        if session.state not in {"saving"}:
            session.state = "mapping"
            session.message = "建图中"
        if now - session.last_preview_at >= 1.0 / self.config.cloud_rate_hz:
            session.last_preview_at = now
            self.preview_updated.emit(
                session.session_id,
                session.accumulator.preview(),
                session.accumulator.bounds(),
            )
        self._emit_snapshot(session)

    def _tick(self) -> None:
        with self._lock:
            session = self._session
            if session is None:
                return
            now = self.clock()
            expired = session.assembler.expire()
            if expired:
                session.dropped_frames += expired
                self._emit_snapshot(session)
            if session.command_attempts < self.config.command_max_attempts and (
                session.state == "negotiating" or session.state == "saving"
            ) and now - session.last_command_at >= self.config.command_retry_seconds:
                self._send_command(session)
            elif session.command_attempts >= self.config.command_max_attempts:
                if session.state == "negotiating":
                    self._interrupt_locked(session, "端侧未确认开始建图指令")
                    return
                if session.state == "saving":
                    self._finalize(session)
                    return
            reference = session.last_complete_frame_at or session.started_monotonic
            silence = now - reference
            if session.state in {"mapping", "warning"} and silence >= self.config.error_timeout_seconds:
                self._interrupt_locked(session, "超过 5 秒未收到完整点云帧")
                return
            if session.state == "mapping" and silence >= self.config.warning_timeout_seconds:
                session.state = "warning"
                session.message = "点云链路警告"
                self._emit_snapshot(session)
            if now - session.last_checkpoint_at >= 5.0 and session.complete_frames:
                self._checkpoint(session, state=session.state, message=session.message)
                session.last_checkpoint_at = now

    def _send_command(self, session: _ActiveSession, *, force: bool = False, reason: str = "") -> None:
        if not force and session.command_attempts >= self.config.command_max_attempts:
            return
        if session.command == "start_mapping":
            payload = {
                "request_id": session.request_id,
                "return_host": self._local_address_for(session.device.ip_address),
                "return_port": self.config.data_port,
                "cloud_rate_hz": self.config.cloud_rate_hz,
                "voxel_size_m": self.config.voxel_size_m,
                "compression": self.config.compression,
                "point_format": self.config.point_format,
                "coordinate_contract": "sensor+map_body+body_sensor",
            }
        else:
            payload = {"request_id": session.request_id, "reason": reason or "用户结束建图"}
        envelope = MapBuildingEnvelope(
            map_id=session.definition.map_id,
            device_id=session.device.device_id,
            session_id=session.session_id,
            message_type=session.command,
            sequence=session.command_attempts,
            sent_at_ns=time.time_ns(),
            payload=payload,
        )
        assert self._socket is not None
        self._socket.sendto(
            self.protocol.encode(envelope),
            (session.device.ip_address, self.config.device_control_port),
        )
        session.command_attempts += 1
        session.last_command_at = self.clock()

    def _send_stop_once(self, session: _ActiveSession, reason: str) -> None:
        if self._socket is None:
            return
        envelope = MapBuildingEnvelope(
            session.definition.map_id,
            session.device.device_id,
            session.session_id,
            "stop_mapping",
            session.command_attempts + 1,
            time.time_ns(),
            {"request_id": uuid.uuid4().hex, "reason": reason},
        )
        self._socket.sendto(self.protocol.encode(envelope), (session.device.ip_address, self.config.device_control_port))

    def _finalize(self, session: _ActiveSession) -> None:
        if session.accumulator.points().size == 0:
            self._interrupt_locked(session, "未收到可保存的完整点云帧")
            return
        session.state = "saving"
        session.message = "正在保存最终点云"
        self._emit_snapshot(session)
        try:
            self._checkpoint(session, state="completed", message="建图完成")
            metadata = MapBuildingResultMetadata(
                session_id=session.session_id,
                device_id=session.device.device_id,
                started_at=session.started_at,
                ended_at=datetime.now(timezone.utc),
                protocol_id=self.config.protocol_id,
                voxel_size_m=self.config.voxel_size_m,
                complete_frames=session.complete_frames,
                dropped_frames=session.dropped_frames,
                received_points=session.accumulator.received_points,
                fused_points=len(session.accumulator.points()),
            )
            definition = self.repository.commit_mapping_result(
                session.definition.map_id, session.session_id, metadata
            )
        except Exception as exc:
            session.state = "failed"
            session.message = str(exc)
            self._emit_snapshot(session)
            self.failed.emit(str(exc))
            self._session = None
            return
        session.state = "completed"
        session.message = "建图完成"
        self._emit_snapshot(session)
        self.completed.emit(definition)
        self._session = None

    def _interrupt_locked(self, session: _ActiveSession, reason: str) -> None:
        try:
            if session.complete_frames:
                self._checkpoint(session, state="interrupted", message=reason)
        except Exception as exc:
            reason = f"{reason}；临时结果保存失败：{exc}"
        session.state = "interrupted"
        session.message = reason
        self._emit_snapshot(session)
        self.failed.emit(reason)
        self._session = None

    def _checkpoint(self, session: _ActiveSession, *, state: str, message: str) -> None:
        if not session.complete_frames:
            return
        payload = self._session_payload(session, state, message)
        self.repository.write_mapping_checkpoint(
            session.definition.map_id,
            session.session_id,
            payload,
            session.accumulator.points(),
            session.trajectory or (),
        )

    def _session_payload(self, session: _ActiveSession, state: str, message: str) -> dict[str, object]:
        ended_at = datetime.now(timezone.utc)
        return {
            "schema_version": 1,
            "protocol_id": self.config.protocol_id,
            "map_id": session.definition.map_id,
            "device_id": session.device.device_id,
            "session_id": session.session_id,
            "state": state,
            "message": message,
            "started_at": session.started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "voxel_size_m": self.config.voxel_size_m,
            "complete_frames": session.complete_frames,
            "dropped_frames": session.dropped_frames,
            "received_points": session.accumulator.received_points,
            "fused_points": len(session.accumulator.points()),
        }

    @staticmethod
    def _metadata_from_payload(payload: dict[str, object]) -> MapBuildingResultMetadata:
        return MapBuildingResultMetadata(
            session_id=str(payload["session_id"]),
            device_id=str(payload["device_id"]),
            started_at=datetime.fromisoformat(str(payload["started_at"])),
            ended_at=datetime.fromisoformat(str(payload["ended_at"])),
            protocol_id=str(payload["protocol_id"]),
            voxel_size_m=float(payload["voxel_size_m"]),
            complete_frames=int(payload["complete_frames"]),
            dropped_frames=int(payload["dropped_frames"]),
            received_points=int(payload["received_points"]),
            fused_points=int(payload["fused_points"]),
        )

    def _snapshot(self, session: _ActiveSession) -> MapBuildingSessionSnapshot:
        return MapBuildingSessionSnapshot(
            map_id=session.definition.map_id,
            device_id=session.device.device_id,
            session_id=session.session_id,
            state=session.state,
            message=session.message,
            started_at=session.started_at,
            complete_frames=session.complete_frames,
            dropped_frames=session.dropped_frames,
            received_points=session.accumulator.received_points,
            fused_points=len(session.accumulator.points()),
            last_data_at=session.last_data_at,
        )

    def _emit_snapshot(self, session: _ActiveSession) -> None:
        self.session_updated.emit(self._snapshot(session))

    @staticmethod
    def _local_address_for(peer_ip: str) -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((peer_ip, 9))
            return str(probe.getsockname()[0])
        except OSError:
            return "127.0.0.1"
        finally:
            probe.close()
