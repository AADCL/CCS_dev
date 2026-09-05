from __future__ import annotations

import math
import socket
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .device_address import device_address_matches
from .models import (
    DeviceLogLevel, DeviceMapBinding, FrameTransform, RelocalizationStatus,
)
from .relocalization_artifacts import (
    RelocalizationArtifactError, RelocalizationHttpServer, build_relocalization_archive,
)
from .relocalization_config import RelocalizationConfig
from .relocalization_protocol import (
    RelocalizationEnvelope, RelocalizationProtocol, RelocalizationProtocolError,
)


@dataclass(frozen=True)
class RelocalizationSnapshot:
    map_id: str
    device_id: str
    session_id: str
    status: RelocalizationStatus
    message: str
    can_download: bool = False
    can_start: bool = False
    can_submit_pose: bool = False
    logs: tuple[str, ...] = ()
    updated_at: datetime = field(
        default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc)
    )


@dataclass
class _Pending:
    envelope: RelocalizationEnvelope
    peer_ip: str
    attempts: int = 0
    sent_at: float = 0.0


STATUS_TEXT = {
    RelocalizationStatus.UNSUPPORTED: "设备不支持重定位",
    RelocalizationStatus.UNKNOWN_SPACE: "未知空间",
    RelocalizationStatus.MAP_TRANSFERRING: "地图下发中",
    RelocalizationStatus.MAP_VERIFYING: "地图校验中",
    RelocalizationStatus.MAP_READY: "已知地图未定位",
    RelocalizationStatus.STACK_STARTING: "重定位功能启动中",
    RelocalizationStatus.AWAITING_POSE: "等待初始位姿",
    RelocalizationStatus.RELOCALIZING: "重定位中",
    RelocalizationStatus.SUCCEEDED: "重定位成功",
    RelocalizationStatus.FAILED: "重定位失败",
}

ACTIVE_RELOCALIZATION_STATUSES = {
    RelocalizationStatus.STACK_STARTING,
    RelocalizationStatus.AWAITING_POSE,
    RelocalizationStatus.RELOCALIZING,
}
_BINDING_PERSIST_INTERVAL_SECONDS = 30.0


class RelocalizationService(QObject):
    snapshot_updated = Signal(object)
    availability_changed = Signal(bool, str)
    protocol_warning = Signal(str)

    def __init__(self, config: RelocalizationConfig, map_repository, source,
                 clock=time.monotonic, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.map_repository = map_repository
        self.source = source
        self.clock = clock
        self.protocol = RelocalizationProtocol(config)
        self._socket: socket.socket | None = None
        self._http: RelocalizationHttpServer | None = None
        self._sequence = 0
        self._snapshots: dict[tuple[str, str], RelocalizationSnapshot] = {}
        self._pending: dict[str, _Pending] = {}
        self._received_sequences: dict[tuple[str, str], int] = {}
        self._tokens: dict[tuple[str, str], str] = {}
        self._live_bindings: dict[tuple[str, str], DeviceMapBinding] = {}
        self._binding_persisted_at: dict[tuple[str, str], float] = {}
        self._dirty_binding_keys: set[tuple[str, str]] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self.available = False
        self.module_message = "重定位服务尚未启动"

    @Slot()
    def start(self) -> None:
        if self._socket is not None:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.config.bind_host, self.config.status_port))
            sock.setblocking(False)
            http = RelocalizationHttpServer(self.config.http_bind_host, self.config.http_port)
            http.start()
        except OSError as exc:
            try:
                sock.close()
            except (OSError, UnboundLocalError):
                pass
            self.module_message = f"重定位服务启动失败：{exc}"
            self.availability_changed.emit(False, self.module_message)
            return
        self._socket, self._http = sock, http
        self.available = True
        self.module_message = f"重定位服务已监听 UDP {self.config.status_port} / TCP {http.port}"
        self._timer.start()
        self.availability_changed.emit(True, self.module_message)

    def stop(self) -> None:
        self._timer.stop()
        self._flush_live_bindings()
        if self._socket is not None:
            self._socket.close()
        if self._http is not None:
            self._http.close()
        self._socket = None
        self._http = None
        self._pending.clear()
        self.available = False

    def snapshot(self, map_id: str, device_id: str) -> RelocalizationSnapshot:
        key = (map_id, device_id.casefold())
        existing = self._snapshots.get(key)
        if existing is not None:
            return existing
        device = self.source.device(device_id)
        profile = self.source.profile(device_id) if device is not None else None
        profile_config = self.config.profile(profile.relocalization_profile if profile else "disabled")
        status = RelocalizationStatus.UNKNOWN_SPACE if profile_config.supported else RelocalizationStatus.UNSUPPORTED
        return RelocalizationSnapshot(
            map_id, device_id, "", status, STATUS_TEXT[status],
            can_download=False,
            updated_at=datetime.now(timezone.utc),
        )

    def binding(self, map_id: str, device_id: str) -> DeviceMapBinding | None:
        key = (map_id, device_id.casefold())
        live = self._live_bindings.get(key)
        if live is not None:
            return live
        profile = self.source.profile(device_id)
        return next((
            item for item in getattr(profile, "map_bindings", ())
            if item.map_id == map_id
        ), None)

    def active_device_id(self, map_id: str) -> str | None:
        active = [
            snapshot.device_id
            for (snapshot_map_id, _device_id), snapshot in self._snapshots.items()
            if snapshot_map_id == map_id
            and snapshot.status in ACTIVE_RELOCALIZATION_STATUSES
        ]
        return active[0] if active else None

    def map_complete(self, map_id: str) -> bool:
        try:
            self.map_repository.pcd_path(map_id)
            self.map_repository.pgm_paths(map_id)
            return True
        except Exception:
            return False

    def negotiate(self, map_id: str, device_id: str) -> RelocalizationSnapshot:
        device = self._require_device(device_id)
        existing = self._snapshots.get((map_id, device_id.casefold()))
        if existing is not None and existing.status in ACTIVE_RELOCALIZATION_STATUSES:
            return existing
        profile = self.source.profile(device_id)
        if hasattr(self.source, "set_device_active_map"):
            self.source.set_device_active_map(device_id, map_id)
        profile_config = self.config.profile(profile.relocalization_profile if profile else "disabled")
        if not profile_config.supported:
            return self._set(map_id, device_id, RelocalizationStatus.UNSUPPORTED, STATUS_TEXT[RelocalizationStatus.UNSUPPORTED])
        self._discard_pending(device_id)
        for key in list(self._live_bindings):
            if key[1] == device_id.casefold():
                self._live_bindings.pop(key, None)
                self._binding_persisted_at.pop(key, None)
                self._dirty_binding_keys.discard(key)
        session_id = uuid.uuid4().hex
        snapshot = RelocalizationSnapshot(
            map_id, device.device_id, session_id, RelocalizationStatus.UNKNOWN_SPACE,
            "正在协商端侧地图状态", can_download=False,
            updated_at=datetime.now(timezone.utc),
        )
        self._store(snapshot, "TX negotiate")
        self._queue(map_id, device.device_id, session_id, "negotiate", {
            "profile": profile.relocalization_profile, "map_complete": self.map_complete(map_id)
        }, device.ip_address)
        return self.snapshot(map_id, device_id)

    def download_map(self, map_id: str, device_id: str) -> None:
        device = self._require_device(device_id)
        snapshot = self.snapshot(map_id, device_id)
        if not snapshot.session_id:
            raise RuntimeError("尚未与端侧建立重定位会话")
        if not self.map_complete(map_id):
            raise RuntimeError("地图缺少完整 PCD/PGM/YAML")
        if self._http is None:
            raise RuntimeError(self.module_message)
        archive, descriptor = build_relocalization_archive(
            self.map_repository, map_id, self.config.max_artifact_bytes)
        old_token = self._tokens.pop((map_id, device_id.casefold()), None)
        if old_token:
            self._http.unregister(old_token)
        token, expires_at = self._http.register(archive, self.config.token_ttl_seconds)
        self._tokens[(map_id, device_id.casefold())] = token
        host = self._local_address_for(device.ip_address)
        self._set(map_id, device_id, RelocalizationStatus.MAP_TRANSFERRING, "正在向端侧下发地图")
        self._queue(map_id, device_id, snapshot.session_id, "map_offer", {
            "url": f"http://{host}:{self._http.port}/relocalization/map.zip?token={token}",
            "expires_at": expires_at, "byte_count": descriptor["byte_count"],
            "sha256": descriptor["sha256"], "manifest": descriptor["manifest"],
        }, device.ip_address)

    def start_stack(self, map_id: str, device_id: str) -> None:
        device = self._require_device(device_id)
        active_device_id = self.active_device_id(map_id)
        if active_device_id and active_device_id.casefold() != device_id.casefold():
            raise RuntimeError(f"设备 {active_device_id} 正在重定位，其他设备暂不可启动")
        snapshot = self.snapshot(map_id, device_id)
        if snapshot.status not in {RelocalizationStatus.MAP_READY, RelocalizationStatus.FAILED,
                                   RelocalizationStatus.SUCCEEDED}:
            raise RuntimeError("端侧地图尚未通过校验")
        profile = self.source.profile(device_id)
        replace_existing = snapshot.status == RelocalizationStatus.SUCCEEDED or any(
            binding.map_id == map_id for binding in getattr(profile, "map_bindings", ())
        )
        if replace_existing:
            self.source.remove_device_map_binding(device_id, map_id)
            key = (map_id, device_id.casefold())
            self._live_bindings.pop(key, None)
            self._binding_persisted_at.pop(key, None)
            self._dirty_binding_keys.discard(key)
        self._set(map_id, device_id, RelocalizationStatus.STACK_STARTING, "正在启动端侧重定位功能")
        self._queue(
            map_id, device_id, snapshot.session_id, "start_stack",
            {"replace_existing": replace_existing}, device.ip_address,
        )

    def submit_initial_pose(self, map_id: str, device_id: str, x: float, y: float, yaw: float) -> None:
        if not all(math.isfinite(value) for value in (x, y, yaw)):
            raise ValueError("初始位姿包含无效数值")
        device = self._require_device(device_id)
        active_device_id = self.active_device_id(map_id)
        if active_device_id and active_device_id.casefold() != device_id.casefold():
            raise RuntimeError(f"初始位姿与活动重定位设备 {active_device_id} 不匹配")
        snapshot = self.snapshot(map_id, device_id)
        if snapshot.status not in {RelocalizationStatus.AWAITING_POSE, RelocalizationStatus.FAILED}:
            raise RuntimeError("端侧尚未准备接收初始位姿")
        self._set(map_id, device_id, RelocalizationStatus.RELOCALIZING, "正在等待重定位结果")
        self._queue(map_id, device_id, snapshot.session_id, "initial_pose", {
            "frame_id": "map", "x": x, "y": y, "yaw": yaw,
            "covariance": {"x": 0.25, "y": 0.25, "yaw": (math.pi / 12.0) ** 2},
        }, device.ip_address)

    def _queue(self, map_id: str, device_id: str, session_id: str, message_type: str,
               payload: dict, peer_ip: str) -> None:
        request_id = uuid.uuid4().hex
        envelope = self._envelope(map_id, device_id, session_id, request_id, message_type, payload)
        pending = _Pending(envelope, peer_ip)
        self._pending[request_id] = pending
        self._send_pending(pending)

    def _envelope(self, map_id, device_id, session_id, request_id, message_type, payload):
        self._sequence += 1
        return RelocalizationEnvelope(
            map_id, device_id, session_id, request_id, message_type,
            self._sequence, time.time_ns(), payload,
        )

    def _send_pending(self, pending: _Pending) -> None:
        if self._socket is None:
            raise RuntimeError(self.module_message)
        self._socket.sendto(
            self.protocol.encode(pending.envelope),
            (pending.peer_ip, self.config.device_control_port),
        )
        pending.attempts += 1
        pending.sent_at = self.clock()

    def process_datagram(self, data: bytes, peer_ip: str) -> None:
        try:
            envelope = self.protocol.decode(data)
        except RelocalizationProtocolError as exc:
            self.protocol_warning.emit(str(exc))
            return
        device = self.source.device(envelope.device_id)
        if device is None or not device_address_matches(device.ip_address, peer_ip):
            self.protocol_warning.emit(f"忽略来源不匹配的重定位消息：{peer_ip}")
            return
        snapshot = self.snapshot(envelope.map_id, envelope.device_id)
        if snapshot.session_id != envelope.session_id:
            self.protocol_warning.emit("忽略过期重定位会话消息")
            return
        key = (envelope.session_id, envelope.message_type)
        previous = self._received_sequences.get(key)
        if previous is not None and envelope.sequence <= previous:
            return
        self._received_sequences[key] = envelope.sequence
        acknowledged = str(envelope.payload.get("request_id", envelope.request_id))
        state = str(envelope.payload.get("state", ""))
        reason = str(envelope.payload.get("reason", ""))
        if not self._transition_allowed(snapshot.status, envelope.message_type, state):
            self.protocol_warning.emit(
                f"忽略非法重定位状态转换：{snapshot.status.value} -> "
                f"{envelope.message_type}/{state}"
            )
            return
        if self._command_complete(envelope.message_type, state):
            self._pending.pop(acknowledged, None)
        else:
            pending = self._pending.get(acknowledged)
            if pending is not None:
                pending.attempts = 0
                pending.sent_at = self.clock()
        if envelope.message_type == "negotiation_status":
            if state in {"map_ready", "localized"}:
                status = RelocalizationStatus.SUCCEEDED if state == "localized" else RelocalizationStatus.MAP_READY
                if state == "localized" and "map_from_odom" in envelope.payload:
                    try:
                        self._save_binding(envelope)
                    except (KeyError, TypeError, ValueError) as exc:
                        self._set(
                            envelope.map_id, envelope.device_id,
                            RelocalizationStatus.FAILED,
                            f"端侧持久变换无效：{exc}",
                        )
                        return
                self._set(envelope.map_id, envelope.device_id, status, STATUS_TEXT[status])
            elif state == "unsupported":
                self._set(envelope.map_id, envelope.device_id, RelocalizationStatus.UNSUPPORTED, reason or STATUS_TEXT[RelocalizationStatus.UNSUPPORTED])
            else:
                self._set(envelope.map_id, envelope.device_id, RelocalizationStatus.UNKNOWN_SPACE, reason or STATUS_TEXT[RelocalizationStatus.UNKNOWN_SPACE])
        elif envelope.message_type == "download_status":
            mapping = {"downloading": RelocalizationStatus.MAP_TRANSFERRING,
                       "verifying": RelocalizationStatus.MAP_VERIFYING,
                       "ready": RelocalizationStatus.MAP_READY,
                       "error": RelocalizationStatus.FAILED}
            status = mapping.get(state, RelocalizationStatus.FAILED)
            self._set(envelope.map_id, envelope.device_id, status, reason or STATUS_TEXT[status])
        elif envelope.message_type == "stack_status":
            mapping = {"starting": RelocalizationStatus.STACK_STARTING,
                       "awaiting_pose": RelocalizationStatus.AWAITING_POSE,
                       "error": RelocalizationStatus.FAILED}
            status = mapping.get(state, RelocalizationStatus.FAILED)
            self._set(envelope.map_id, envelope.device_id, status, reason or STATUS_TEXT[status])
        elif envelope.message_type == "relocalization_result":
            if state == "relocalizing":
                self._set(
                    envelope.map_id, envelope.device_id,
                    RelocalizationStatus.RELOCALIZING,
                    STATUS_TEXT[RelocalizationStatus.RELOCALIZING],
                )
            elif state == "succeeded":
                try:
                    self._save_binding(envelope)
                except (KeyError, TypeError, ValueError) as exc:
                    self._set(envelope.map_id, envelope.device_id, RelocalizationStatus.FAILED, f"返回变换无效：{exc}")
                else:
                    if snapshot.status == RelocalizationStatus.SUCCEEDED:
                        refreshed = replace(
                            snapshot, updated_at=datetime.now(timezone.utc)
                        )
                        self._snapshots[(
                            snapshot.map_id, snapshot.device_id.casefold()
                        )] = refreshed
                        self.snapshot_updated.emit(refreshed)
                    else:
                        self._set(
                            envelope.map_id, envelope.device_id,
                            RelocalizationStatus.SUCCEEDED,
                            STATUS_TEXT[RelocalizationStatus.SUCCEEDED],
                        )
            else:
                self._set(envelope.map_id, envelope.device_id, RelocalizationStatus.FAILED, reason or STATUS_TEXT[RelocalizationStatus.FAILED])
        elif envelope.message_type == "command_error":
            self._set(envelope.map_id, envelope.device_id, RelocalizationStatus.FAILED, reason or "端侧命令执行失败")
        elif envelope.message_type == "session_heartbeat":
            refreshed = replace(snapshot, updated_at=datetime.now(timezone.utc))
            self._snapshots[(snapshot.map_id, snapshot.device_id.casefold())] = refreshed
            self.snapshot_updated.emit(refreshed)

    @staticmethod
    def _command_complete(message_type: str, state: str) -> bool:
        return (
            message_type in {"negotiation_status", "command_error"}
            or message_type == "download_status" and state in {"ready", "error"}
            or message_type == "stack_status" and state in {"awaiting_pose", "error"}
            or message_type == "relocalization_result" and state in {"succeeded", "failed"}
        )

    @staticmethod
    def _transition_allowed(
        current: RelocalizationStatus, message_type: str, state: str,
    ) -> bool:
        allowed = {
            "negotiation_status": {
                RelocalizationStatus.UNKNOWN_SPACE,
                RelocalizationStatus.MAP_READY,
                RelocalizationStatus.SUCCEEDED,
                RelocalizationStatus.UNSUPPORTED,
            },
            "download_status": {
                RelocalizationStatus.MAP_TRANSFERRING,
                RelocalizationStatus.MAP_VERIFYING,
                RelocalizationStatus.MAP_READY,
                RelocalizationStatus.FAILED,
            },
            "stack_status": {
                RelocalizationStatus.STACK_STARTING,
                RelocalizationStatus.AWAITING_POSE,
                RelocalizationStatus.FAILED,
            },
            "relocalization_result": {
                RelocalizationStatus.RELOCALIZING,
                RelocalizationStatus.SUCCEEDED,
                RelocalizationStatus.FAILED,
            },
            "command_error": {RelocalizationStatus.FAILED},
            "session_heartbeat": {current},
        }
        targets = allowed.get(message_type)
        if targets is None:
            return False
        if message_type == "download_status":
            return current in {
                RelocalizationStatus.MAP_TRANSFERRING,
                RelocalizationStatus.MAP_VERIFYING,
            } and ({
                "downloading": RelocalizationStatus.MAP_TRANSFERRING,
                "verifying": RelocalizationStatus.MAP_VERIFYING,
                "ready": RelocalizationStatus.MAP_READY,
                "error": RelocalizationStatus.FAILED,
            }.get(state) in targets)
        if message_type == "stack_status":
            return current == RelocalizationStatus.STACK_STARTING and state in {
                "starting", "awaiting_pose", "error",
            }
        if message_type == "relocalization_result":
            if current == RelocalizationStatus.RELOCALIZING:
                return state in {"relocalizing", "succeeded", "failed"}
            return current == RelocalizationStatus.SUCCEEDED and state in {
                "succeeded", "failed",
            }
        if message_type == "negotiation_status":
            return current == RelocalizationStatus.UNKNOWN_SPACE and state in {
                "map_required", "map_ready", "localized", "unsupported",
            }
        return True

    def _save_binding(self, envelope: RelocalizationEnvelope) -> None:
        raw = envelope.payload["map_from_odom"]
        transform = FrameTransform(**{
            key: float(raw[key]) for key in ("x", "y", "z", "qx", "qy", "qz", "qw")
        })
        profile = self.source.profile(envelope.device_id)
        profile_config = self.config.profile(profile.relocalization_profile)
        binding = DeviceMapBinding(
            envelope.map_id, str(envelope.payload.get("map_frame", "map")),
            str(envelope.payload.get("odom_frame", profile_config.odom_frame)), transform,
            datetime.now(timezone.utc), profile_config.pose_source,
        )
        key = (envelope.map_id, envelope.device_id.casefold())
        self._live_bindings[key] = binding
        now = self.clock()
        last_persisted = self._binding_persisted_at.get(key)
        if (
            last_persisted is None
            or now - last_persisted >= _BINDING_PERSIST_INTERVAL_SECONDS
        ):
            self.source.upsert_device_map_binding(envelope.device_id, binding)
            self._binding_persisted_at[key] = now
            self._dirty_binding_keys.discard(key)
        else:
            self._dirty_binding_keys.add(key)

    def _flush_live_bindings(self) -> None:
        for key in tuple(self._dirty_binding_keys):
            binding = self._live_bindings.get(key)
            if binding is None:
                continue
            self.source.upsert_device_map_binding(key[1], binding)
            self._binding_persisted_at[key] = self.clock()
        self._dirty_binding_keys.clear()

    def _discard_pending(self, device_id: str) -> None:
        folded = device_id.casefold()
        for request_id, pending in list(self._pending.items()):
            if pending.envelope.device_id.casefold() == folded:
                self._pending.pop(request_id, None)

    def _set(self, map_id: str, device_id: str, status: RelocalizationStatus,
             message: str) -> RelocalizationSnapshot:
        current = self.snapshot(map_id, device_id)
        updated = replace(
            current, status=status, message=message,
            can_download=status in {RelocalizationStatus.UNKNOWN_SPACE, RelocalizationStatus.FAILED}
                         and bool(current.session_id) and self.map_complete(map_id),
            can_start=(
                status in {RelocalizationStatus.MAP_READY, RelocalizationStatus.SUCCEEDED}
                or status == RelocalizationStatus.FAILED and (
                    current.can_start
                    or current.status == RelocalizationStatus.STACK_STARTING
                )
            ),
            can_submit_pose=(
                status == RelocalizationStatus.AWAITING_POSE
                or status == RelocalizationStatus.FAILED and (
                    current.can_submit_pose
                    or current.status in {
                        RelocalizationStatus.AWAITING_POSE,
                        RelocalizationStatus.RELOCALIZING,
                    }
                )
            ),
            updated_at=datetime.now(timezone.utc),
        )
        return self._store(updated, f"RX {status.value}: {message}")

    def _store(self, snapshot: RelocalizationSnapshot, log: str) -> RelocalizationSnapshot:
        logs = deque(snapshot.logs, maxlen=200)
        logs.append(f"{datetime.now().strftime('%H:%M:%S')} {log}")
        snapshot = replace(snapshot, logs=tuple(logs), updated_at=datetime.now(timezone.utc))
        self._snapshots[(snapshot.map_id, snapshot.device_id.casefold())] = snapshot
        self.snapshot_updated.emit(snapshot)
        level = DeviceLogLevel.ERROR if snapshot.status == RelocalizationStatus.FAILED else DeviceLogLevel.INFO
        self.source.append_external_log(snapshot.device_id, level, snapshot.message)
        return snapshot

    def _require_device(self, device_id: str):
        if not self.available or self._socket is None:
            raise RuntimeError(self.module_message)
        device = self.source.device(device_id)
        if device is None or not device.ip_address:
            raise ValueError(f"设备 {device_id} 不存在或缺少有效地址")
        return device

    @staticmethod
    def _local_address_for(peer_ip: str) -> str:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((peer_ip, 9))
            return str(probe.getsockname()[0])
        finally:
            probe.close()

    @Slot()
    def _tick(self) -> None:
        if self._socket is None:
            return
        for _ in range(64):
            try:
                data, peer = self._socket.recvfrom(self.config.max_datagram_bytes + 1)
            except BlockingIOError:
                break
            except OSError:
                return
            self.process_datagram(data, str(peer[0]))
        now = self.clock()
        for request_id, pending in list(self._pending.items()):
            current = self.snapshot(pending.envelope.map_id, pending.envelope.device_id)
            if current.session_id != pending.envelope.session_id:
                self._pending.pop(request_id, None)
                continue
            if now - pending.sent_at < self.config.command_retry_seconds:
                continue
            if pending.attempts >= self.config.command_max_attempts:
                self._pending.pop(request_id, None)
                self._set(pending.envelope.map_id, pending.envelope.device_id,
                          RelocalizationStatus.FAILED, f"端侧未确认 {pending.envelope.message_type}")
            else:
                self._send_pending(pending)
        for key, snapshot in list(self._snapshots.items()):
            if not snapshot.session_id or snapshot.status == RelocalizationStatus.UNSUPPORTED:
                continue
            age = (datetime.now(timezone.utc) - snapshot.updated_at).total_seconds()
            if age <= self.config.session_timeout_seconds:
                continue
            expired = replace(
                snapshot, session_id="", status=RelocalizationStatus.UNKNOWN_SPACE,
                message="重定位会话已失效", can_download=self.map_complete(snapshot.map_id),
                can_start=False, can_submit_pose=False,
            )
            self._discard_pending(snapshot.device_id)
            self._store(expired, "RX session expired")
        if self._http is not None:
            self._http.cleanup()
