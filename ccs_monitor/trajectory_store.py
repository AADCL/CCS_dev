from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from .device_map_context import resolve_device_map_context
from .models import ConnectionStatus
from .runtime_paths import application_root

LOGGER = logging.getLogger(__name__)


@dataclass
class _Track:
    device_id: str
    connection_id: str
    directory: Path
    last_seen: float
    disconnected_at: float | None = None
    points: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    segment_key: object = None
    segment_id: str = ""
    last_position: tuple | None = None
    chunk_index: int = 0
    chunk_points: int = 0
    online: bool = False
    revision: int = 0
    written_metadata: dict | None = None


class DeviceTrajectoryStore(QObject):
    """Connection-scoped archive with bounded display memory and one disk writer."""

    changed = Signal()
    storage_error = Signal(str)
    expired = Signal(str)
    MAX_POINTS = 10000
    DISPLAY_POINTS = 2000
    CHUNK_POINTS = 4096
    TTL_SECONDS = 120.0

    def __init__(self, source, telemetry_store=None, relocalization_service=None,
                 root=None, *, clock=time.time, start_timers=True, parent=None):
        super().__init__(parent)
        self.source = source
        self.telemetry_store = telemetry_store
        self.relocalization_service = relocalization_service
        self.root = Path(root) if root is not None else application_root() / "data" / "trajectories"
        startup_error = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            startup_error = exc
        self.clock = clock
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._tracks = {}
        self._deletions = []
        self._cache = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._restored = threading.Event()
        self.last_error = None
        if startup_error is not None:
            self._report_error(startup_error)
        self._load_metadata()
        if hasattr(source, "devices_updated"):
            source.devices_updated.connect(self.update_connections)
        if telemetry_store is not None:
            telemetry_store.telemetry_updated.connect(self.observe)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)
        if start_timers:
            self._timer.start()
        self._writer = threading.Thread(target=self._run_writer, daemon=True, name="ccs-trajectories")
        self._writer.start()
        self.update_connections(source.snapshots())

    def _load_metadata(self):
        for file in self.root.glob("*/session.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                device_id = data["device_id"]
                if not isinstance(device_id, str) or not device_id or not isinstance(data["connection_id"], str):
                    continue
                connection_id = str(uuid.UUID(hex=data["connection_id"])).replace("-", "")
                expected = hashlib.sha256(device_id.encode()).hexdigest()[:20] + "-" + connection_id
                if file.parent.name != expected or data.get("schema_version") != 1:
                    continue
                last_seen = float(data["last_seen"])
                disconnected = data.get("disconnected_at")
                disconnected = float(disconnected) if disconnected is not None else None
                if not math.isfinite(last_seen) or (disconnected is not None and not math.isfinite(float(disconnected))):
                    continue
                chunk_index, chunk_points = int(data.get("chunk_index", 0)), int(data.get("chunk_points", 0))
                if chunk_index < 0 or not 0 <= chunk_points <= self.CHUNK_POINTS:
                    continue
                track = _Track(device_id, connection_id, file.parent, last_seen, disconnected,
                               chunk_index=chunk_index, chunk_points=chunk_points)
                if self.clock() - (disconnected if disconnected is not None else last_seen) >= self.TTL_SECONDS:
                    self._deletions.append(file.parent)
                else:
                    previous = self._tracks.get(device_id)
                    if previous is None or previous.last_seen < last_seen:
                        if previous:
                            self._deletions.append(previous.directory)
                        self._tracks[device_id] = track
                    else:
                        self._deletions.append(file.parent)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                LOGGER.warning("忽略损坏的轨迹元数据 %s: %s", file.name, exc)

    @staticmethod
    def _compact(points):
        if len(points) <= DeviceTrajectoryStore.DISPLAY_POINTS:
            return points
        # Keep the beginning and latest point; never truncate the start of the connection.
        while len(points) > DeviceTrajectoryStore.DISPLAY_POINTS:
            points = points[::2] + ([points[-1]] if len(points) % 2 == 0 else [])
        return points

    def _restore_points(self):
        with self._lock:
            tracks = list(self._tracks.values())
        for track in tracks:
            points = []
            for file in sorted(track.directory.glob("chunk-*.jsonl")):
                try:
                    with file.open("r", encoding="utf-8") as handle:
                        for line in handle:
                            try:
                                point = json.loads(line)
                                if (not isinstance(point, list) or len(point) != 6
                                        or not isinstance(point[0], str) or not isinstance(point[1], str)
                                        or not all(type(value) in (int, float) and math.isfinite(value) for value in point[2:])):
                                    continue
                                if points and points[-1] == tuple(point):
                                    continue
                                points.append(tuple(point))
                                if len(points) > self.DISPLAY_POINTS:
                                    points = self._compact(points)
                            except (ValueError, TypeError):
                                continue  # A crash may leave a partial final JSONL line.
                except (OSError, UnicodeError) as exc:
                    self._report_error(exc)
            with self._lock:
                if self._tracks.get(track.device_id) is track:
                    track.points = self._compact(points + track.points)
                    track.revision += 1
                    self._cache.clear()
        self._restored.set()
        self.changed.emit()

    def update_connections(self, devices):
        now = self.clock()
        valid = {device.device_id for device in devices}
        with self._lock:
            for device_id in tuple(self._tracks):
                if device_id not in valid:
                    self._expire(device_id)
            for device in devices:
                track = self._tracks.get(device.device_id)
                online = device.connection_status == ConnectionStatus.ONLINE
                if hasattr(self.source, "connection_timing"):
                    last_seen, disconnected = self.source.connection_timing(device.device_id)
                else:
                    last_seen = device.last_heartbeat_at.timestamp() if device.last_heartbeat_at else (now if online else None)
                    disconnected = None
                origin = disconnected if disconnected is not None else last_seen
                if track and origin is None:
                    origin = track.disconnected_at if track.disconnected_at is not None else track.last_seen
                if track and now - (track.disconnected_at if track.disconnected_at is not None else track.last_seen) >= self.TTL_SECONDS:
                    self._expire(device.device_id)
                    track = None
                if track is None and online and last_seen is not None and now - last_seen < self.TTL_SECONDS:
                    connection_id = uuid.uuid4().hex
                    name = hashlib.sha256(device.device_id.encode()).hexdigest()[:20] + "-" + connection_id
                    track = _Track(device.device_id, connection_id, self.root / name, last_seen)
                    self._tracks[device.device_id] = track
                if track is None:
                    continue
                if not online or (online and not track.online):
                    track.segment_key = None
                track.online = online
                if last_seen is not None:
                    track.last_seen = last_seen
                track.disconnected_at = disconnected
        self.tick(refresh=False)

    def observe(self, device_id, telemetry):
        context = resolve_device_map_context(self.source, self.relocalization_service, telemetry, device_id)
        pose = context.map_pose
        if pose is None or not context.map_id or pose.sample_age_seconds > 2:
            return
        position = (pose.x, pose.y, pose.z)
        if not all(math.isfinite(value) for value in position):
            return
        profile = self.source.profile(device_id)
        binding = (self.relocalization_service.binding(context.map_id, device_id) if self.relocalization_service
                   else next((item for item in profile.map_bindings if item.map_id == context.map_id), None))
        segment_key = (context.map_id, getattr(telemetry, "session_id", None),
                       repr(binding.map_from_odom) if binding else None)
        with self._lock:
            track = self._tracks.get(device_id)
            if track is None or not track.online:
                return
            if segment_key != track.segment_key:
                track.segment_key = segment_key
                track.segment_id = uuid.uuid4().hex
                track.last_position = None
            record = (context.map_id, track.segment_id, *position, self.clock())
            display_changed = track.last_position is None or math.dist(position, track.last_position) >= 0.02
            if display_changed:
                track.last_position = position
                track.points.append(record)
                track.points = self._compact(track.points)
            # A failed/full disk cannot turn into an unbounded RAM queue.
            if len(track.pending) < self.MAX_POINTS:
                track.pending.append(record)
            else:
                self._report_error("轨迹写入积压已达上限；部分新点未能持久化")
            if display_changed:
                track.revision += 1
                self._cache.clear()

    def trails(self, map_id, device_ids=None):
        with self._lock:
            ids = tuple(sorted(device_ids if device_ids is not None else self._tracks))
            key = (map_id, ids, tuple((device_id, self._tracks[device_id].revision)
                                     for device_id in ids if device_id in self._tracks))
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            result = {}
            for device_id in ids:
                track = self._tracks.get(device_id)
                if track is None:
                    continue
                positions, previous = [], None
                for point in track.points:
                    if point[0] != map_id:
                        continue
                    if previous is not None and previous != point[1]:
                        positions.append((math.nan, math.nan, math.nan))
                    positions.append(tuple(point[2:5]))
                    previous = point[1]
                if positions:
                    result[device_id] = tuple(positions)
            if len(self._cache) >= 4:
                self._cache.clear()
            self._cache[key] = result
            return result

    def clear(self, device_id):
        """Explicit maintenance/deletion API; display switches must never call this."""
        with self._lock:
            self._expire(device_id)
        self._wake.set()

    def tick(self, refresh=True):
        if refresh:
            self.update_connections(self.source.snapshots())
            return
        expired = False
        with self._lock:
            now = self.clock()
            for device_id, track in tuple(self._tracks.items()):
                origin = track.disconnected_at if track.disconnected_at is not None else track.last_seen
                if now - origin >= self.TTL_SECONDS:
                    self._expire(device_id)
                    expired = True
        if expired:
            self.changed.emit()

    def _expire(self, device_id):
        track = self._tracks.pop(device_id, None)
        if track:
            self._deletions.append(track.directory)
            self._cache.clear()
            self.expired.emit(device_id)
            self.changed.emit()

    def _report_error(self, exc):
        message = str(exc)
        if self.last_error != message:
            self.last_error = message
            LOGGER.warning("轨迹存储异常：%s", message)
            self.storage_error.emit(message)

    def _run_writer(self):
        try:
            self._restore_points()
            while not self._stop.is_set():
                self._wake.wait(1.0)
                self._wake.clear()
                self._write_pending()
            self._write_pending()
        except Exception as exc:
            self._report_error(exc)
        finally:
            self._restored.set()

    def _write_pending(self):
        with self._io_lock:
            with self._lock:
                deletions, self._deletions = self._deletions, []
                work = [(track, track.pending) for track in self._tracks.values()]
                for track, _ in work:
                    track.pending = []
            for directory in deletions:
                try:
                    if directory.resolve().parent != self.root.resolve():
                        raise ValueError("轨迹清理路径不在数据目录内")
                    if directory.exists():
                        shutil.rmtree(directory)
                except OSError as exc:
                    self._report_error(exc)
                    with self._lock:
                        self._deletions.append(directory)
            for track, pending in work:
                with self._lock:
                    if self._tracks.get(track.device_id) is not track:
                        continue
                try:
                    track.directory.mkdir(parents=True, exist_ok=True)
                    while pending:
                        if track.chunk_points >= self.CHUNK_POINTS:
                            track.chunk_index += 1
                            track.chunk_points = 0
                        count = min(len(pending), self.CHUNK_POINTS - track.chunk_points)
                        file = track.directory / f"chunk-{track.chunk_index:08d}.jsonl"
                        if file.exists():
                            with file.open("rb+") as repair:
                                repair.seek(0, 2)
                                end = repair.tell()
                                if end:
                                    repair.seek(end - 1)
                                    if repair.read(1) != b"\n":
                                        offset = end
                                        last_newline = -1
                                        while offset and last_newline < 0:
                                            size = min(offset, 8192)
                                            offset -= size
                                            repair.seek(offset)
                                            block = repair.read(size)
                                            index = block.rfind(b"\n")
                                            if index >= 0:
                                                last_newline = offset + index
                                        repair.truncate(last_newline + 1)
                        with file.open("a", encoding="utf-8") as handle:
                            handle.writelines(json.dumps(point, separators=(",", ":")) + "\n" for point in pending[:count])
                            handle.flush()
                            os.fsync(handle.fileno())
                        track.chunk_points += count
                        pending = pending[count:]
                    with self._lock:
                        metadata = dict(schema_version=1, device_id=track.device_id,
                                        connection_id=track.connection_id, last_seen=track.last_seen,
                                        disconnected_at=track.disconnected_at,
                                        chunk_index=track.chunk_index, chunk_points=track.chunk_points)
                    if metadata != track.written_metadata:
                        temporary = track.directory / "session.tmp"
                        temporary.write_text(json.dumps(metadata), encoding="utf-8")
                        os.replace(temporary, track.directory / "session.json")
                        track.written_metadata = metadata
                except (OSError, ValueError) as exc:
                    self._report_error(exc)
                    with self._lock:
                        if self._tracks.get(track.device_id) is track:
                            track.pending = (pending + track.pending)[-self.MAX_POINTS:]

    def flush(self):
        self._restored.wait(5)
        self._write_pending()

    def close(self):
        self._timer.stop()
        self._stop.set()
        self._wake.set()
        self._writer.join(timeout=5)
        if self._writer.is_alive():
            self._report_error("轨迹后台保存尚未完成")
