from __future__ import annotations

import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

from PySide6.QtCore import QObject, Signal

from .map_building import (
    CloudFrameAssembler, MapBuildingEnvelope, MapBuildingJobSnapshot,
    MapBuildingProtocol, MapBuildingProtocolError, MapBuildingSessionSnapshot,
    VoxelMapAccumulator,
)
from .map_building_config import MapBuildingConfig
from .map_fusion import MapFusionRepository, MapFusionRunner, transform_points
from .map_repository import MapRepository, MapRepositoryError
from .pgm_fusion import PgmDownloadCoordinator
from .models import (
    DeviceSnapshot, MapBuildMode, MapBuildProvenance, MapBuildingResultMetadata,
    MapDefinition, MapFusionAlgorithm, MapTransform,
)


@dataclass
class _DeviceSession:
    device: DeviceSnapshot
    transform: MapTransform
    session_id: str
    request_id: str
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
    seen_cloud_sequences: set[int] = field(default_factory=set)
    trajectory: list[tuple[object, ...]] = field(default_factory=list)
    last_checkpoint_at: float = 0.0
    excluded: bool = False


@dataclass
class _ActiveJob:
    definition: MapDefinition
    job_id: str
    primary_device_id: str
    algorithm: MapFusionAlgorithm
    sessions: dict[str, _DeviceSession]
    started_at: datetime
    started_monotonic: float
    state: str = "negotiating"
    message: str = "正在与所有端侧设备协商"
    last_preview_at: float = 0.0


class MapBuildingService(QObject):
    session_updated = Signal(object)
    preview_updated = Signal(str, object, object)
    completed = Signal(object)
    failed = Signal(str)
    availability_changed = Signal(bool, str)
    job_updated = Signal(object)
    device_session_updated = Signal(object)
    degraded = Signal(object)
    pgm_source_updated = Signal(object)
    pgm_source_completed = Signal(object)
    pgm_download_failed = Signal(str, str)
    pgm_download_completed = Signal(object)

    def __init__(self, config: MapBuildingConfig, repository: MapRepository,
                 fusion_repository: MapFusionRepository | None = None,
                 fusion_runner: MapFusionRunner | None = None, *,
                 clock: Callable[[], float] = time.monotonic,
                 socket_factory: Callable[..., socket.socket] = socket.socket,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.repository = repository
        self.fusion_repository = fusion_repository or MapFusionRepository()
        self.fusion_runner = fusion_runner or MapFusionRunner()
        self.clock = clock
        self.socket_factory = socket_factory
        self.protocol = MapBuildingProtocol(config)
        self.pgm_download = PgmDownloadCoordinator(
            config, self._send_pgm_envelope, return_host=config.bind_host, clock=clock,
        )
        self.pgm_download.source_updated.connect(self.pgm_source_updated)
        self.pgm_download.source_completed.connect(self.pgm_source_completed)
        self.pgm_download.failed.connect(self.pgm_download_failed)
        self.pgm_download.all_completed.connect(self.pgm_download_completed)
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.RLock()
        self._job: _ActiveJob | None = None
        self.available = False
        self.module_message = "UDP 建图模块尚未启动"

    @property
    def active(self) -> bool:
        with self._lock:
            return self._job is not None or self.pgm_download.active

    @property
    def mapping_active(self) -> bool:
        with self._lock:
            return self._job is not None

    @property
    def pgm_download_active(self) -> bool:
        return self.pgm_download.active

    def device_active(self, device_id: str) -> bool:
        with self._lock:
            return self._job is not None and device_id.casefold() in self._job.sessions

    @property
    def current_job_snapshot(self) -> MapBuildingJobSnapshot | None:
        with self._lock:
            return self._job_snapshot(self._job) if self._job else None

    @property
    def current_snapshot(self) -> MapBuildingSessionSnapshot | None:
        with self._lock:
            job = self._job
            if job is None:
                return None
            snapshot = self._job_snapshot(job)
            primary = next(
                (item for item in snapshot.device_sessions
                 if item.device_id.casefold() == job.primary_device_id.casefold()),
                snapshot.device_sessions[0],
            )
            return MapBuildingSessionSnapshot(
                map_id=snapshot.map_id, device_id=primary.device_id,
                session_id=job.job_id, state=snapshot.state, message=snapshot.message,
                started_at=snapshot.started_at, complete_frames=snapshot.complete_frames,
                dropped_frames=snapshot.dropped_frames, received_points=snapshot.received_points,
                fused_points=snapshot.fused_points, last_data_at=snapshot.last_data_at,
            )

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
        self.pgm_download.cancel()
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
        return self.start_job(
            definition, [device], device.device_id,
            [MapTransform(device.device_id, True)], self.fusion_repository.default_algorithm(),
        )

    def start_job(self, definition: MapDefinition, devices: Iterable[DeviceSnapshot],
                  primary_device_id: str, transforms: Iterable[MapTransform],
                  algorithm: MapFusionAlgorithm | str | None = None) -> str:
        if not self.available or self._socket is None:
            raise RuntimeError(self.module_message)
        device_list = list(devices)
        if not device_list:
            raise ValueError("至少选择一台建图设备")
        ids = [item.device_id.casefold() for item in device_list]
        if len(set(ids)) != len(ids):
            raise ValueError("建图设备不能重复")
        if any(not item.ip_address for item in device_list):
            raise ValueError("所有建图设备都必须配置有效 IP 地址")
        primary_folded = primary_device_id.casefold()
        if primary_folded not in ids:
            raise ValueError("主设备必须属于建图设备")
        transform_map = {item.source_id.casefold(): item for item in transforms}
        if set(transform_map) != set(ids):
            raise ValueError("每台设备都必须配置且只能配置一组外参")
        primary_transform = transform_map[primary_folded]
        if (not primary_transform.is_primary
                or primary_transform.translation_m != (0.0, 0.0, 0.0)
                or primary_transform.rotation_rpy_deg != (0.0, 0.0, 0.0)
                or sum(item.is_primary for item in transform_map.values()) != 1):
            raise ValueError("主设备必须是唯一使用单位变换的坐标系")
        selected_algorithm = self._resolve_algorithm(algorithm)
        with self._lock:
            if self._job is not None or self.pgm_download.active:
                raise RuntimeError("建图或 PGM 下载任务正在运行")
            now = datetime.now(timezone.utc)
            started = self.clock()
            job_id = uuid.uuid4().hex
            sessions: dict[str, _DeviceSession] = {}
            for device in device_list:
                session_id = job_id if len(device_list) == 1 else uuid.uuid4().hex
                sessions[device.device_id.casefold()] = _DeviceSession(
                    device=device, transform=transform_map[device.device_id.casefold()],
                    session_id=session_id, request_id=uuid.uuid4().hex,
                    assembler=CloudFrameAssembler(self.config, self.clock),
                    accumulator=VoxelMapAccumulator(
                        self.config.voxel_size_m, self.config.max_accumulated_voxels,
                        self.config.max_preview_points,
                    ), last_checkpoint_at=started,
                )
            job = _ActiveJob(
                definition, job_id, primary_device_id, selected_algorithm,
                sessions, now, started,
            )
            self._job = job
            try:
                for session in sessions.values():
                    self._send_command(job, session, force=True)
            except OSError:
                self._job = None
                raise
            self._emit_job(job)
            return job_id

    def stop_mapping(self, reason: str = "用户结束建图") -> None:
        self.stop_job(reason)

    def stop_job(self, reason: str = "用户结束建图") -> None:
        with self._lock:
            job = self._job
            if job is None:
                return
            included = [item for item in job.sessions.values() if not item.excluded]
            if not included:
                self._interrupt_job_locked(job, "没有可保存的建图设备")
                return
            job.state = "saving"
            job.message = "正在结束所有端侧会话并融合"
            for session in included:
                session.state = "saving"
                session.message = "正在结束端侧会话"
                session.command = "stop_mapping"
                session.request_id = uuid.uuid4().hex
                session.command_attempts = 0
                self._send_command(job, session, force=True, reason=reason)
            self._emit_job(job)

    def continue_without_device(self, device_id: str) -> None:
        with self._lock:
            job = self._job
            if job is None:
                raise RuntimeError("没有活动建图任务")
            session = job.sessions.get(device_id.casefold())
            if session is None:
                raise ValueError("设备不属于当前建图任务")
            if session.device.device_id.casefold() == job.primary_device_id.casefold():
                raise ValueError("主设备掉线时不能降级继续，请中止任务")
            session.excluded = True
            session.state = "excluded"
            session.message = "已从联合建图中剔除"
            remaining = [item for item in job.sessions.values() if not item.excluded]
            job.state = "mapping"
            job.message = f"已剔除 {session.device.device_name}，剩余 {len(remaining)} 台设备"
            self._emit_job(job)

    def interrupt_mapping(self, reason: str = "建图会话中断") -> None:
        with self._lock:
            if self._job is not None:
                self._interrupt_job_locked(self._job, reason)

    def start_pgm_download(self, target_map_id: str, sources, job_root) -> None:
        if not self.available or self._socket is None:
            raise RuntimeError(self.module_message)
        with self._lock:
            if self._job is not None or self.pgm_download.active:
                raise RuntimeError("实时建图与 PGM 下载不能同时运行")
            source_list = list(sources)
            if source_list:
                self.pgm_download.return_host = self._local_address_for(source_list[0].device_ip)
            self.pgm_download.start(target_map_id, source_list, job_root)

    def resume_pgm_download(self, target_map_id: str, sources, job_root) -> None:
        self.start_pgm_download(target_map_id, sources, job_root)

    def retry_pgm_download(self) -> None:
        self.pgm_download.retry_current()

    def remove_failed_pgm_source(self) -> None:
        self.pgm_download.remove_current()

    def cancel_pgm_download(self) -> None:
        self.pgm_download.cancel()

    def save_interrupted_session(self, map_id: str, session_id: str) -> MapDefinition:
        sessions = self.repository.interrupted_sessions(map_id)
        payload = next((item for item in sessions if item.get("session_id") == session_id), None)
        if payload is None:
            raise MapRepositoryError("临时建图会话不存在")
        return self.repository.commit_mapping_result(map_id, session_id, self._metadata_from_payload(payload))

    def save_interrupted_job(self, map_id: str, job_id: str,
                             algorithm_id: str | None = None) -> MapDefinition:
        jobs = self.repository.interrupted_mapping_jobs(map_id)
        payload = next((item for item in jobs if item.get("job_id") == job_id), None)
        if payload is None:
            raise MapRepositoryError("临时联合建图任务不存在")
        definition = self.repository.map_by_id(map_id)
        if definition is None:
            raise MapRepositoryError("地图不存在")
        algorithm = self._resolve_algorithm(algorithm_id or str(payload["algorithm_id"]))
        root = self.repository.mapping_session_directory(map_id, job_id)
        excluded = {str(item) for item in payload.get("excluded_device_ids", [])}
        transforms_by_id = {
            str(item["source_id"]): MapTransform(
                str(item["source_id"]), bool(item.get("is_primary", False)),
                tuple(float(value) for value in item["translation_m"]),
                tuple(float(value) for value in item["rotation_rpy_deg"]),
            )
            for item in payload.get("transforms", [])
        }
        device_ids = [
            str(item) for item in payload.get("devices", [])
            if str(item) not in excluded and (root / str(item) / "partial.pcd").is_file()
        ]
        if not device_ids or any(item not in transforms_by_id for item in device_ids):
            raise MapRepositoryError("临时联合建图任务的设备或外参不完整")
        output = root / "plugin-output.pcd"
        self.fusion_runner.run(
            algorithm, [root / item / "partial.pcd" for item in device_ids],
            definition.frame_id, [transforms_by_id[item] for item in device_ids], output,
        )
        started_at = datetime.fromisoformat(str(payload["started_at"]))
        provenance = MapBuildProvenance(
            MapBuildMode.MULTI if len(payload.get("devices", [])) > 1 else MapBuildMode.SINGLE,
            job_id, str(payload["primary_device_id"]), tuple(device_ids),
            tuple(transforms_by_id[item] for item in device_ids), algorithm.algorithm_id,
            algorithm.version, algorithm.sha256, tuple(sorted(excluded)),
            started_at, datetime.now(timezone.utc),
        )
        return self.repository.commit_fusion_to_existing(map_id, job_id, output, provenance)

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
        if self.pgm_download.handle_envelope(envelope, peer_ip):
            return
        with self._lock:
            job = self._job
            if job is None or envelope.map_id != job.definition.map_id:
                return
            session = job.sessions.get(envelope.device_id.casefold())
            if session is None or envelope.session_id != session.session_id:
                raise MapBuildingProtocolError("数据报设备或会话标识不一致")
            if peer_ip != session.device.ip_address:
                raise MapBuildingProtocolError("数据报来源 IP 与设备配置不一致")
            if envelope.message_type == "cloud_chunk":
                if envelope.sequence in session.seen_cloud_sequences:
                    return
                session.seen_cloud_sequences.add(envelope.sequence)
                if len(session.seen_cloud_sequences) > 100000:
                    floor = max(session.seen_cloud_sequences) - 50000
                    session.seen_cloud_sequences = {
                        item for item in session.seen_cloud_sequences if item >= floor
                    }
                self._handle_cloud(job, session, envelope)
                return
            if envelope.sequence <= session.last_sequence:
                return
            session.last_sequence = envelope.sequence
            if envelope.message_type == "command_ack":
                self._handle_ack(job, session, envelope)
            elif envelope.message_type == "session_heartbeat" and session.state not in {"saving", "stopped"}:
                session.message = "端侧建图会话在线"
                self._emit_job(job)
            elif envelope.message_type == "session_status":
                state = envelope.payload["state"]
                if state == "error":
                    self._mark_degraded(job, session, envelope.payload.get("reason") or "端侧传感器错误")
                elif state == "stopped" and session.state == "saving":
                    session.state = "stopped"
                    self._finalize_if_ready(job)

    def _handle_ack(self, job: _ActiveJob, session: _DeviceSession,
                    envelope: MapBuildingEnvelope) -> None:
        payload = envelope.payload
        if payload["request_id"] != session.request_id or payload["command"] != session.command:
            return
        if not payload["accepted"]:
            self._mark_degraded(job, session, payload.get("reason") or "端侧拒绝建图指令")
            return
        if session.command == "start_mapping":
            session.state = "ready"
            session.message = "端侧已就绪"
            active = [item for item in job.sessions.values() if not item.excluded]
            if active and all(item.state in {"ready", "mapping", "warning"} for item in active):
                job.state = "mapping"
                job.message = f"{len(active)} 台设备联合建图中" if len(active) > 1 else "建图中"
                for item in active:
                    if item.state == "ready":
                        item.state = "mapping"
                        item.message = "建图中"
        else:
            session.state = "stopped"
            session.message = "端侧已停止"
            self._finalize_if_ready(job)
        self._emit_job(job)

    def _handle_cloud(self, job: _ActiveJob, session: _DeviceSession,
                      envelope: MapBuildingEnvelope) -> None:
        completed = session.assembler.push(envelope)
        if completed is None:
            return
        points, trajectory = completed
        session.accumulator.add(points)
        session.trajectory.append(trajectory)
        session.complete_frames += 1
        now = self.clock()
        session.last_complete_frame_at = now
        session.last_data_at = datetime.now(timezone.utc)
        if job.state != "negotiating" and session.state not in {"saving", "excluded"}:
            session.state = "mapping"
            session.message = "建图中"
        if now - job.last_preview_at >= 1.0 / min(self.config.cloud_rate_hz, 5.0):
            job.last_preview_at = now
            self._emit_preview(job)
        self._emit_job(job)

    def _emit_preview(self, job: _ActiveJob) -> None:
        combined = VoxelMapAccumulator(
            self.config.voxel_size_m, self.config.max_accumulated_voxels,
            self.config.max_preview_points,
        )
        for session in job.sessions.values():
            points = session.accumulator.points()
            if session.excluded or not len(points):
                continue
            combined.add(transform_points(points, session.transform))
        if len(combined.points()):
            self.preview_updated.emit(job.job_id, combined.preview(), combined.bounds())

    def _tick(self) -> None:
        self.pgm_download.tick()
        with self._lock:
            job = self._job
            if job is None:
                return
            now = self.clock()
            for session in list(job.sessions.values()):
                if session.excluded or session.state in {"stopped", "failed"}:
                    continue
                expired = session.assembler.expire()
                if expired:
                    session.dropped_frames += expired
                if session.state in {"negotiating", "saving"}:
                    if (session.command_attempts < self.config.command_max_attempts
                            and now - session.last_command_at >= self.config.command_retry_seconds):
                        self._send_command(job, session)
                    elif session.command_attempts >= self.config.command_max_attempts:
                        if session.state == "saving":
                            session.state = "stopped"
                            self._finalize_if_ready(job)
                        elif len(job.sessions) == 1:
                            self._interrupt_job_locked(job, "端侧未确认开始建图指令")
                            return
                        else:
                            self._mark_degraded(job, session, "端侧未确认开始建图指令")
                    continue
                reference = session.last_complete_frame_at or job.started_monotonic
                silence = now - reference
                if (session.state in {"mapping", "warning", "ready"}
                        and silence >= self.config.error_timeout_seconds):
                    if len(job.sessions) == 1:
                        self._interrupt_job_locked(job, "超过 5 秒未收到完整点云帧")
                        return
                    self._mark_degraded(job, session, "超过 5 秒未收到完整点云帧")
                elif session.state == "mapping" and silence >= self.config.warning_timeout_seconds:
                    session.state = "warning"
                    session.message = "点云链路警告"
                if now - session.last_checkpoint_at >= 5.0 and session.complete_frames:
                    self._checkpoint_job_device(job, session, "mapping", job.message)
                    session.last_checkpoint_at = now
            self._emit_job(job)

    def _send_command(self, job: _ActiveJob, session: _DeviceSession, *,
                      force: bool = False, reason: str = "") -> None:
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
                "job_id": job.job_id,
                "role": "primary" if session.transform.is_primary else "secondary",
                "primary_device_id": job.primary_device_id,
            }
        else:
            payload = {"request_id": session.request_id, "reason": reason or "用户结束建图"}
        envelope = MapBuildingEnvelope(
            job.definition.map_id, session.device.device_id, session.session_id,
            session.command, session.command_attempts, time.time_ns(), payload,
        )
        assert self._socket is not None
        self._socket.sendto(
            self.protocol.encode(envelope),
            (session.device.ip_address, self.config.device_control_port),
        )
        session.command_attempts += 1
        session.last_command_at = self.clock()

    def _send_stop_once(self, job: _ActiveJob, session: _DeviceSession, reason: str) -> None:
        if self._socket is None:
            return
        envelope = MapBuildingEnvelope(
            job.definition.map_id, session.device.device_id, session.session_id,
            "stop_mapping", session.command_attempts + 1, time.time_ns(),
            {"request_id": uuid.uuid4().hex, "reason": reason},
        )
        self._socket.sendto(
            self.protocol.encode(envelope),
            (session.device.ip_address, self.config.device_control_port),
        )

    def _finalize_if_ready(self, job: _ActiveJob) -> None:
        included = [item for item in job.sessions.values() if not item.excluded]
        if included and all(item.state == "stopped" for item in included):
            self._finalize_job(job)

    def _finalize_job(self, job: _ActiveJob) -> None:
        included = [
            item for item in job.sessions.values()
            if not item.excluded and item.complete_frames
        ]
        if not included:
            self._interrupt_job_locked(job, "未收到可保存的完整点云帧")
            return
        job.state = "saving"
        job.message = "正在执行最终融合算法"
        self._emit_job(job)
        try:
            for session in included:
                self._checkpoint_job_device(job, session, "completed", "采集完成")
            root = self.repository.mapping_session_directory(job.definition.map_id, job.job_id)
            output = root / "plugin-output.pcd"
            transforms = [session.transform for session in included]
            inputs = [root / session.device.device_id / "partial.pcd" for session in included]
            self.fusion_runner.run(
                job.algorithm, inputs, job.definition.frame_id, transforms, output,
            )
            ended_at = datetime.now(timezone.utc)
            provenance = MapBuildProvenance(
                MapBuildMode.MULTI if len(job.sessions) > 1 else MapBuildMode.SINGLE,
                job.job_id, job.primary_device_id,
                tuple(item.device.device_id for item in included), tuple(transforms),
                job.algorithm.algorithm_id, job.algorithm.version, job.algorithm.sha256,
                tuple(item.device.device_id for item in job.sessions.values() if item.excluded),
                job.started_at, ended_at,
            )
            legacy_metadata = None
            if len(job.sessions) == 1:
                only = included[0]
                legacy_metadata = MapBuildingResultMetadata(
                    job.job_id, only.device.device_id, job.started_at, ended_at,
                    self.config.protocol_id, self.config.voxel_size_m,
                    only.complete_frames, only.dropped_frames,
                    only.accumulator.received_points, len(only.accumulator.points()),
                )
            definition = self.repository.commit_fusion_to_existing(
                job.definition.map_id, job.job_id, output, provenance, legacy_metadata,
            )
        except Exception as exc:
            job.state = "failed"
            job.message = f"融合失败，临时结果已保留：{exc}"
            self._emit_job(job)
            self.failed.emit(job.message)
            self._job = None
            return
        job.state = "completed"
        job.message = "联合建图完成"
        self._emit_job(job)
        self.completed.emit(definition)
        self._job = None

    def _send_pgm_envelope(self, envelope: MapBuildingEnvelope, peer_ip: str) -> None:
        udp_socket = self._socket
        if udp_socket is None:
            raise RuntimeError("UDP 建图 socket 未启动")
        udp_socket.sendto(
            self.protocol.encode(envelope),
            (peer_ip, self.config.device_control_port),
        )

    def _mark_degraded(self, job: _ActiveJob, session: _DeviceSession, reason: str) -> None:
        if session.state == "failed":
            return
        session.state = "failed"
        session.message = reason
        job.state = "degraded"
        job.message = f"设备 {session.device.device_name} 中断，请选择剔除后继续或中止"
        self._emit_job(job)
        self.degraded.emit(self._job_snapshot(job))

    def _interrupt_job_locked(self, job: _ActiveJob, reason: str) -> None:
        errors: list[str] = []
        for session in job.sessions.values():
            try:
                self._send_stop_once(job, session, reason)
                if session.complete_frames:
                    if len(job.sessions) == 1:
                        self._checkpoint_legacy(job, session, "interrupted", reason)
                    else:
                        self._checkpoint_job_device(job, session, "interrupted", reason)
            except Exception as exc:
                errors.append(str(exc))
            session.state = "interrupted"
            session.message = reason
        if errors:
            reason = f"{reason}；临时结果保存失败：{'；'.join(errors)}"
        job.state = "interrupted"
        job.message = reason
        self._emit_job(job)
        self.failed.emit(reason)
        self._job = None

    def _checkpoint_legacy(self, job: _ActiveJob, session: _DeviceSession,
                           state: str, message: str) -> None:
        self.repository.write_mapping_checkpoint(
            job.definition.map_id, session.session_id,
            self._session_payload(job, session, state, message),
            session.accumulator.points(), session.trajectory,
        )

    def _checkpoint_job_device(self, job: _ActiveJob, session: _DeviceSession,
                               state: str, message: str) -> None:
        payload = {
            "schema_version": 1, "protocol_id": self.config.protocol_id,
            "map_id": job.definition.map_id, "job_id": job.job_id,
            "primary_device_id": job.primary_device_id, "state": state,
            "message": message, "algorithm_id": job.algorithm.algorithm_id,
            "started_at": job.started_at.isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "devices": [item.device.device_id for item in job.sessions.values()],
            "transforms": [
                {"source_id": item.transform.source_id,
                 "is_primary": item.transform.is_primary,
                 "translation_m": list(item.transform.translation_m),
                 "rotation_rpy_deg": list(item.transform.rotation_rpy_deg)}
                for item in job.sessions.values()
            ],
            "algorithm_version": job.algorithm.version,
            "algorithm_sha256": job.algorithm.sha256,
            "excluded_device_ids": [
                item.device.device_id for item in job.sessions.values() if item.excluded
            ],
        }
        self.repository.write_mapping_job_checkpoint(
            job.definition.map_id, job.job_id, payload, session.device.device_id,
            session.accumulator.points(), session.trajectory,
        )

    def _session_payload(self, job: _ActiveJob, session: _DeviceSession,
                         state: str, message: str) -> dict[str, object]:
        return {
            "schema_version": 1, "protocol_id": self.config.protocol_id,
            "map_id": job.definition.map_id, "device_id": session.device.device_id,
            "session_id": session.session_id, "state": state, "message": message,
            "started_at": job.started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "voxel_size_m": self.config.voxel_size_m,
            "complete_frames": session.complete_frames,
            "dropped_frames": session.dropped_frames,
            "received_points": session.accumulator.received_points,
            "fused_points": len(session.accumulator.points()),
        }

    @staticmethod
    def _metadata_from_payload(payload: dict[str, object]) -> MapBuildingResultMetadata:
        return MapBuildingResultMetadata(
            session_id=str(payload["session_id"]), device_id=str(payload["device_id"]),
            started_at=datetime.fromisoformat(str(payload["started_at"])),
            ended_at=datetime.fromisoformat(str(payload["ended_at"])),
            protocol_id=str(payload["protocol_id"]),
            voxel_size_m=float(payload["voxel_size_m"]),
            complete_frames=int(payload["complete_frames"]),
            dropped_frames=int(payload["dropped_frames"]),
            received_points=int(payload["received_points"]),
            fused_points=int(payload["fused_points"]),
        )

    def _session_snapshot(self, job: _ActiveJob,
                          session: _DeviceSession) -> MapBuildingSessionSnapshot:
        return MapBuildingSessionSnapshot(
            map_id=job.definition.map_id, device_id=session.device.device_id,
            session_id=session.session_id, state=session.state, message=session.message,
            started_at=job.started_at, complete_frames=session.complete_frames,
            dropped_frames=session.dropped_frames,
            received_points=session.accumulator.received_points,
            fused_points=len(session.accumulator.points()), last_data_at=session.last_data_at,
        )

    def _job_snapshot(self, job: _ActiveJob) -> MapBuildingJobSnapshot:
        return MapBuildingJobSnapshot(
            job.definition.map_id, job.job_id, job.state, job.message,
            job.primary_device_id, job.algorithm.algorithm_id,
            tuple(self._session_snapshot(job, item) for item in job.sessions.values()),
            tuple(item.device.device_id for item in job.sessions.values() if item.excluded),
            job.started_at,
        )

    def _emit_job(self, job: _ActiveJob) -> None:
        snapshot = self._job_snapshot(job)
        self.job_updated.emit(snapshot)
        for item in snapshot.device_sessions:
            self.device_session_updated.emit(item)
        legacy = self.current_snapshot
        if legacy is not None:
            self.session_updated.emit(legacy)

    def _resolve_algorithm(self, algorithm: MapFusionAlgorithm | str | None) -> MapFusionAlgorithm:
        if algorithm is None:
            return self.fusion_repository.default_algorithm()
        algorithm_id = algorithm.algorithm_id if isinstance(algorithm, MapFusionAlgorithm) else algorithm
        # Algorithm objects contain a runtime path. Always reload from this
        # installation's repository so an object created before relocation cannot
        # leak another machine's absolute path into the fusion worker.
        selected = self.fusion_repository.algorithm(algorithm_id)
        if selected is None:
            raise ValueError(f"融合算法不存在：{algorithm_id}")
        if not selected.enabled:
            raise ValueError("融合算法已禁用")
        return selected

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
