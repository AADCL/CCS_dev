from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
from functools import wraps
from collections import OrderedDict, deque
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from .runtime_paths import application_root
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QObject, Signal

from .models import DeviceProfile, DeviceSnapshot, MapDefinition, utc_now
from .task_models import (
    EdgeTaskStatus, DeviceSubtask,
    TaskDefinition,
    TaskDefinitionStatus,
    TaskEvent,
    TaskEventLevel,
    TaskExecutionSnapshot,
    TaskSafetySettings,
    TaskWaypoint,
)


TASK_SCHEMA_VERSION = 2
DEFAULT_TASK_ROOT = application_root() / "data" / "task_server"


class TaskRepositoryError(RuntimeError):
    pass


class DuplicateTaskNameError(TaskRepositoryError):
    pass


def sanitize_task_name(name: str) -> str:
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name.strip())
    normalized = re.sub(r"\s+", "_", normalized).strip(" ._")
    if not normalized:
        raise TaskRepositoryError("任务名称不能为空或仅包含非法字符")
    return normalized[:80]


def map_fingerprint(definition: MapDefinition) -> str:
    payload = {
        "map_id": definition.map_id,
        "updated_at": definition.updated_at.isoformat(),
        "point_count": definition.point_count,
        "pcd": definition.pcd_path,
        "pgm": definition.pgm.__dict__ if definition.pgm else None,
        "bounds": definition.bounds.__dict__ if definition.bounds else None,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _transaction(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        notices = []
        try:
            with self._write_lock:
                outermost = self._transaction_depth == 0
                self._transaction_depth += 1
                try:
                    return method(self, *args, **kwargs)
                finally:
                    self._transaction_depth -= 1
                    if outermost:
                        notices, self._notifications = self._notifications, []
        finally:
            # Slots may enter the service lock. Never notify while holding the
            # repository lock (the UDP worker acquires those locks in reverse).
            for signal, values in notices:
                signal.emit(*values)
    return locked


class TaskRepository(QObject):
    tasks_updated = Signal(object)
    execution_updated = Signal(object)
    events_updated = Signal(str)
    event_appended = Signal(object)

    def __init__(self, root: str | Path = DEFAULT_TASK_ROOT, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.trash_root = self.root / ".trash"
        self.root.mkdir(parents=True, exist_ok=True)
        self.trash_root.mkdir(parents=True, exist_ok=True)
        self._tasks: list[TaskDefinition] = []
        self._write_lock = threading.RLock()
        self._transaction_depth = 0
        self._notifications = []
        self._recent_events = OrderedDict()
        self._execution_stats = None
        self.load_all()

    def _notify(self, signal, *values):
        if self._transaction_depth:
            self._notifications.append((signal, values))
        else:
            signal.emit(*values)

    @_transaction
    def load_all(self) -> list[TaskDefinition]:
        tasks: list[TaskDefinition] = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                tasks.append(self._read_task(directory / "task.json", directory.name))
            except TaskRepositoryError as exc:
                timestamp = datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc)
                tasks.append(TaskDefinition(
                    f"error:{directory.name}", directory.name, "", "未知地图", "map", "",
                    timestamp, timestamp, status=TaskDefinitionStatus.ERROR,
                    directory_name=directory.name, error_message=str(exc),
                ))
        tasks.sort(key=lambda item: item.updated_at, reverse=True)
        self._tasks = tasks
        return list(tasks)

    @_transaction
    def tasks(self) -> list[TaskDefinition]:
        return list(self._tasks)

    @_transaction
    def task_by_id(self, task_id: str) -> TaskDefinition | None:
        return next((item for item in self._tasks if item.task_id == task_id), None)

    @_transaction
    def update_device_reference(
        self, original_device_id: str, profile: DeviceProfile
    ) -> tuple[TaskDefinition, ...]:
        folded = original_device_id.casefold()
        originals: list[TaskDefinition] = []
        updated_items: list[TaskDefinition] = []
        for task in self._tasks:
            if task.error_message or not any(
                item.device_id.casefold() == folded for item in task.subtasks
            ):
                continue
            originals.append(task)
            subtasks = tuple(
                replace(
                    item,
                    device_id=profile.device_id,
                    device_name=profile.device_name,
                    device_type=profile.device_type,
                    ip_address=profile.ip_address,
                    revision=item.revision + 1,
                    delivered_revision=None,
                )
                if item.device_id.casefold() == folded else item
                for item in task.subtasks
            )
            updated_items.append(replace(task, subtasks=subtasks, updated_at=utc_now()))
        try:
            for task in updated_items:
                self._write_task(task)
        except Exception:
            for task in originals:
                self._write_task(task)
            raise
        if updated_items:
            self._refresh()
        return tuple(originals)

    @_transaction
    def restore_definitions(self, definitions: Iterable[TaskDefinition]) -> None:
        restored = tuple(definitions)
        for task in restored:
            self._write_task(task)
        if restored:
            self._refresh()

    def audit_device_reference_update(
        self, task_ids: Iterable[str], old_device_id: str, new_device_id: str
    ) -> None:
        for task_id in task_ids:
            self.append_audit(
                task_id,
                "device_identity_migrated",
                f"设备档案由 {old_device_id} 更新为 {new_device_id}，子任务修订已失效",
                device_id=new_device_id,
                payload={"old_device_id": old_device_id, "new_device_id": new_device_id},
            )

    @_transaction
    def create(
        self,
        name: str,
        map_definition: MapDefinition,
        devices: Iterable[DeviceSnapshot],
        *,
        now: datetime | None = None,
    ) -> TaskDefinition:
        display_name = name.strip()
        safe_name = sanitize_task_name(display_name)
        selected = tuple(devices)
        if not selected:
            raise TaskRepositoryError("至少选择一台任务设备")
        if not (map_definition.pcd_path or map_definition.pgm):
            raise TaskRepositoryError("所选地图没有可用 PCD 或 PGM 图层")
        self._ensure_unique_name(display_name)
        created_at = (now or utc_now()).astimezone(timezone.utc)
        directory = self.root / str(uuid.uuid4().hex)
        directory.mkdir()
        subtasks = tuple(DeviceSubtask(
            uuid.uuid4().hex, item.device_id, item.device_name, item.device_type, item.ip_address,
            cruise_speed_mps=0.1 if item.device_type == "AGV" else 1.0,
        ) for item in selected)
        task = TaskDefinition(
            uuid.uuid4().hex, display_name, map_definition.map_id, map_definition.name,
            map_definition.frame_id, map_fingerprint(map_definition), created_at, created_at,
            subtasks, directory_name=directory.name,
        )
        try:
            self._write_task(task)
            self.load_all()
            self.append_audit(task.task_id, "task_created", "创建任务", payload={
                "map_id": task.map_id, "device_ids": [item.device_id for item in subtasks]
            })
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        self._refresh()
        return self.task_by_id(task.task_id) or task

    @_transaction
    def update_subtask(self, task_id: str, subtask: DeviceSubtask, *, reason: str = "保存子任务") -> TaskDefinition:
        task = self._require_task(task_id)
        self._validate_subtask(subtask)
        found = False
        updated_subtasks: list[DeviceSubtask] = []
        for current in task.subtasks:
            if current.subtask_id == subtask.subtask_id:
                found = True
                if current.same_definition(subtask):
                    return task
                updated_subtasks.append(replace(
                    subtask, revision=current.revision + 1, delivered_revision=None,
                    edge_status=EdgeTaskStatus.NO_TASK, edge_revision=None,
                    edge_message="", edge_updated_at=None,
                ))
            else:
                updated_subtasks.append(current)
        if not found:
            raise TaskRepositoryError("子任务不存在")
        ready = all(item.is_valid for item in updated_subtasks)
        updated = replace(
            task, subtasks=tuple(updated_subtasks), updated_at=utc_now(),
            status=TaskDefinitionStatus.READY if ready else TaskDefinitionStatus.DRAFT,
        )
        self._write_task(updated)
        self._write_subtask_static(updated, next(item for item in updated.subtasks if item.subtask_id == subtask.subtask_id))
        self.append_audit(task_id, "subtask_saved", reason, device_id=subtask.device_id, payload={
            "revision": next(item.revision for item in updated_subtasks if item.subtask_id == subtask.subtask_id),
            "waypoint_count": len(subtask.waypoints),
        })
        self._refresh()
        return self.task_by_id(task_id) or updated

    @_transaction
    def save_subtasks(self, task_id: str, subtasks: tuple[DeviceSubtask, ...]) -> TaskDefinition:
        task = self._require_task(task_id)
        if {item.subtask_id for item in subtasks} != {item.subtask_id for item in task.subtasks}:
            raise TaskRepositoryError("同步下发必须包含全部任务设备")
        for item in subtasks:
            self._validate_subtask(item)
        for item in subtasks:
            self.update_subtask(task_id, item)
        return self._require_task(task_id)

    @_transaction
    def invalidate_delivery(self, task_id: str, device_id: str, *, receiving: bool = False) -> None:
        task = self._require_task(task_id)
        items = tuple(replace(item, delivered_revision=None if receiving else item.delivered_revision,
                              edge_status=EdgeTaskStatus.RECEIVING if receiving else EdgeTaskStatus.TASK_EXISTS,
                              edge_revision=None, edge_message="", edge_updated_at=None)
                      if item.device_id == device_id else item for item in task.subtasks)
        if items != task.subtasks:
            self._write_task(replace(task, subtasks=items, updated_at=utc_now()))
            self._refresh()

    def _write_subtask_static(self, task: TaskDefinition, subtask: DeviceSubtask) -> Path:
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        safe_device = sanitize_task_name(subtask.device_id)
        path = self.root / task.directory_name / (f"{stamp}_{safe_device}.json")
        self._atomic_json(path, {
            "schema_version": 2, "task_id": task.task_id, "subtask_id": subtask.subtask_id,
            "device_id": subtask.device_id, "map_id": task.map_id, "frame_id": task.frame_id,
            "revision": subtask.revision, "default_altitude_m": subtask.default_altitude_m,
            "cruise_speed_mps": subtask.cruise_speed_mps,
            "start_delay_seconds": subtask.start_delay_seconds,
            "waypoints": [point.__dict__ for point in subtask.waypoints],
        })
        return path

    @_transaction
    def mark_delivered(self, task_id: str, device_id: str, revision: int) -> TaskDefinition:
        task = self._require_task(task_id)
        subtasks = tuple(
            replace(item, delivered_revision=revision)
            if item.device_id.casefold() == device_id.casefold() and item.revision == revision else item
            for item in task.subtasks
        )
        if not any(item.device_id.casefold() == device_id.casefold() and item.revision == revision for item in task.subtasks):
            raise TaskRepositoryError("设备或子任务修订不匹配")
        if subtasks == task.subtasks:
            return task
        updated = replace(task, subtasks=subtasks, updated_at=utc_now())
        self._write_task(updated)
        self.append_audit(task_id, "subtask_delivered", "子任务下发成功", device_id=device_id, payload={
            "revision": revision
        })
        self._refresh()
        return self.task_by_id(task_id) or updated

    @_transaction
    def update_edge_status(self, task_id: str, subtask: DeviceSubtask) -> TaskDefinition:
        task = self._require_task(task_id)
        current = next((item for item in task.subtasks if item.subtask_id == subtask.subtask_id), None)
        if current is None or current.device_id != subtask.device_id:
            raise TaskRepositoryError("子任务不存在")
        if current.revision != subtask.revision:
            raise TaskRepositoryError("忽略过期子任务状态")
        runtime = replace(current, edge_status=subtask.edge_status, edge_revision=subtask.edge_revision,
                          edge_message=subtask.edge_message, edge_updated_at=subtask.edge_updated_at)
        if (current.edge_status, current.edge_revision, current.edge_message) == (runtime.edge_status, runtime.edge_revision, runtime.edge_message):
            return task
        items = tuple(runtime if item.subtask_id == current.subtask_id else item for item in task.subtasks)
        updated = replace(task, subtasks=items, updated_at=utc_now())
        self._write_task(updated)
        self._refresh()
        return self.task_by_id(task_id) or updated

    @_transaction
    def update_safety(self, task_id: str, settings: TaskSafetySettings) -> TaskDefinition:
        self._validate_safety(settings)
        task = self._require_task(task_id)
        updated = replace(task, safety=settings, updated_at=utc_now())
        self._write_task(updated)
        self.append_audit(task_id, "safety_updated", "更新冲突检查参数", payload=settings.__dict__)
        self._refresh()
        return self.task_by_id(task_id) or updated

    @_transaction
    def delete(self, task_id: str) -> Path:
        task = self._require_task(task_id)
        source = self.root / task.directory_name
        target = self.trash_root / task.directory_name
        suffix = 2
        while target.exists():
            target = self.trash_root / f"{task.directory_name}_{suffix}"
            suffix += 1
        shutil.move(str(source), str(target))
        self._tasks = [item for item in self._tasks if item.task_id != task_id]
        self._recent_events.pop(task_id, None)
        self._execution_stats = None
        self._refresh()
        return target

    @_transaction
    def append_audit(
        self,
        task_id: str,
        event_type: str,
        message: str,
        *,
        level: TaskEventLevel = TaskEventLevel.INFO,
        device_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        task = self._require_task(task_id)
        self._append_jsonl(self.root / task.directory_name / "audit.jsonl", {
            "timestamp": utc_now().isoformat(), "event_type": event_type, "message": message,
            "level": level.value, "task_id": task_id, "device_id": device_id,
            "payload": payload or {},
        })
        self._notify(self.events_updated, task_id)

    def audit_events(self, task_id: str) -> list[TaskEvent]:
        task = self._require_task(task_id)
        return self._read_events(self.root / task.directory_name / "audit.jsonl")

    @_transaction
    def create_execution(self, task_id: str, snapshot: TaskExecutionSnapshot) -> Path:
        task = self._require_task(task_id)
        directory = self.root / task.directory_name / "executions" / snapshot.execution_id
        directory.mkdir(parents=True, exist_ok=False)
        self._atomic_json(directory / "snapshot.json", self._serialize_execution(snapshot))
        if self._execution_stats is not None:
            self._execution_stats = (self._execution_stats[0] + 1, snapshot)
        self.append_execution_event(task_id, snapshot.execution_id, "execution_created", "创建任务执行")
        self._notify(self.execution_updated, snapshot)
        return directory

    @_transaction
    def update_execution(self, snapshot: TaskExecutionSnapshot) -> None:
        task = self._require_task(snapshot.task_id)
        directory = self.root / task.directory_name / "executions" / snapshot.execution_id
        if not directory.is_dir():
            raise TaskRepositoryError("任务执行记录不存在")
        self._atomic_json(directory / "snapshot.json", self._serialize_execution(snapshot))
        if self._execution_stats is not None and self._execution_stats[1] is not None and self._execution_stats[1].execution_id == snapshot.execution_id:
            self._execution_stats = (self._execution_stats[0], snapshot)
        self._notify(self.execution_updated, snapshot)

    @_transaction
    def append_execution_event(
        self,
        task_id: str,
        execution_id: str,
        event_type: str,
        message: str,
        *,
        level: TaskEventLevel = TaskEventLevel.INFO,
        device_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        task = self._require_task(task_id)
        directory = self.root / task.directory_name / "executions" / execution_id
        directory.mkdir(parents=True, exist_ok=True)
        self._append_jsonl(directory / "events.jsonl", {
            "timestamp": utc_now().isoformat(), "event_type": event_type, "message": message,
            "level": level.value, "task_id": task_id, "execution_id": execution_id,
            "device_id": device_id, "payload": payload or {},
        })
        self._notify(self.events_updated, task_id)

    def execution_events(self, task_id: str, execution_id: str) -> list[TaskEvent]:
        task = self._require_task(task_id)
        return self._read_events(
            self.root / task.directory_name / "executions" / execution_id / "events.jsonl"
        )

    def executions(self, task_id: str | None = None) -> list[TaskExecutionSnapshot]:
        tasks = [self._require_task(task_id)] if task_id else [item for item in self._tasks if item.status != TaskDefinitionStatus.ERROR]
        result: list[TaskExecutionSnapshot] = []
        for task in tasks:
            root = self.root / task.directory_name / "executions"
            if not root.is_dir():
                continue
            for directory in root.iterdir():
                try:
                    result.append(self._parse_execution(json.loads(
                        (directory / "snapshot.json").read_text(encoding="utf-8")
                    )))
                except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    continue
        result.sort(key=lambda item: item.created_at, reverse=True)
        return result

    @_transaction
    def execution_count(self) -> int:
        self._ensure_execution_stats()
        return self._execution_stats[0]

    @_transaction
    def latest_execution(self) -> TaskExecutionSnapshot | None:
        self._ensure_execution_stats()
        return self._execution_stats[1]

    def _ensure_execution_stats(self) -> None:
        if self._execution_stats is None:
            records = self.executions()
            self._execution_stats = (len(records), records[0] if records else None)

    @_transaction
    def recent_events(self, task_id: str, limit: int = 500) -> list[TaskEvent]:
        limit = max(0, min(limit, 500))
        if task_id not in self._recent_events:
            task = self._require_task(task_id)
            directory = self.root / task.directory_name
            events = self._read_events(directory / "audit.jsonl", limit=500)
            for file in (directory / "executions").glob("*/events.jsonl"):
                events.extend(self._read_events(file, limit=500))
                events = sorted(events, key=lambda event: event.timestamp)[-500:]
            self._recent_events[task_id] = deque(sorted(events, key=lambda event: event.timestamp)[-500:], maxlen=500)
        self._recent_events.move_to_end(task_id)
        while len(self._recent_events) > 4:
            self._recent_events.popitem(last=False)
        return list(self._recent_events[task_id])[-limit:] if limit else []

    def _require_task(self, task_id: str) -> TaskDefinition:
        task = self.task_by_id(task_id)
        if task is None:
            raise TaskRepositoryError(f"任务不存在：{task_id}")
        if task.status == TaskDefinitionStatus.ERROR:
            raise TaskRepositoryError(task.error_message or "任务数据损坏")
        return task

    def _ensure_unique_name(self, name: str) -> None:
        folded = name.casefold()
        if any(item.name.casefold() == folded for item in self._tasks):
            raise DuplicateTaskNameError(f"任务名称已存在：{name}")

    def _refresh(self) -> None:
        self._tasks.sort(key=lambda item: item.updated_at, reverse=True)
        self._notify(self.tasks_updated, self.tasks())

    @staticmethod
    def _validate_subtask(subtask: DeviceSubtask) -> None:
        if subtask.layer_mode not in {"pointcloud", "grid"}:
            raise TaskRepositoryError("子任务图层模式无效")
        if not 2 <= len(subtask.waypoints) <= 500:
            raise TaskRepositoryError("每个子任务必须包含 2 到 500 个任务点")
        if not math.isfinite(subtask.default_altitude_m):
            raise TaskRepositoryError("默认高度必须为有限数值")
        if not math.isfinite(subtask.cruise_speed_mps) or subtask.cruise_speed_mps <= 0:
            raise TaskRepositoryError("巡航速度必须大于零")
        if not math.isfinite(subtask.start_delay_seconds) or subtask.start_delay_seconds < 0:
            raise TaskRepositoryError("启动延迟不能为负数")
        ids: set[str] = set()
        for waypoint in subtask.waypoints:
            if waypoint.waypoint_id in ids or not waypoint.waypoint_id:
                raise TaskRepositoryError("任务点 ID 为空或重复")
            ids.add(waypoint.waypoint_id)
            if not all(math.isfinite(value) for value in (waypoint.x, waypoint.y, waypoint.z)):
                raise TaskRepositoryError("任务点坐标必须为有限数值")

    @staticmethod
    def _validate_safety(settings: TaskSafetySettings) -> None:
        values = (settings.horizontal_distance_m, settings.vertical_distance_m, settings.time_margin_seconds)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise TaskRepositoryError("冲突检查参数必须为大于零的有限数值")

    def _read_task(self, path: Path, directory_name: str) -> TaskDefinition:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") not in {1, TASK_SCHEMA_VERSION}:
                raise ValueError("schema_version 不受支持")
            safety = TaskSafetySettings(**{key: float(value) for key, value in payload["safety"].items()})
            self._validate_safety(safety)
            subtasks = tuple(self._parse_subtask(item) for item in payload["subtasks"])
            task = TaskDefinition(
                str(payload["task_id"]), str(payload["name"]), str(payload["map_id"]),
                str(payload["map_name"]), str(payload["frame_id"]), str(payload["map_fingerprint"]),
                datetime.fromisoformat(str(payload["created_at"])),
                datetime.fromisoformat(str(payload["updated_at"])), subtasks, safety,
            TaskDefinitionStatus(str(payload["status"])), directory_name,
            )
            if not task.task_id or not task.name or not task.map_id or not task.subtasks:
                raise ValueError("任务 ID、名称、地图和设备不能为空")
            return task
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise TaskRepositoryError(f"任务元数据无效：{exc}") from exc

    def _write_task(self, task: TaskDefinition) -> None:
        directory = self.root / task.directory_name
        self._atomic_json(directory / "task.json", {
            "schema_version": TASK_SCHEMA_VERSION,
            "task_id": task.task_id, "name": task.name, "map_id": task.map_id,
            "map_name": task.map_name, "frame_id": task.frame_id,
            "map_fingerprint": task.map_fingerprint,
            "created_at": task.created_at.isoformat(), "updated_at": task.updated_at.isoformat(),
            "status": task.status.value, "safety": task.safety.__dict__,
            "subtasks": [self._serialize_subtask(item) for item in task.subtasks],
        })

        self._tasks = [task if item.task_id == task.task_id else item for item in self._tasks]
        if not any(item.task_id == task.task_id for item in self._tasks):
            self._tasks.append(task)

    @staticmethod
    def _serialize_subtask(item: DeviceSubtask) -> dict[str, Any]:
        return {
            "subtask_id": item.subtask_id, "device_id": item.device_id,
            "device_name": item.device_name, "device_type": item.device_type,
            "ip_address": item.ip_address, "layer_mode": item.layer_mode,
            "default_altitude_m": item.default_altitude_m,
            "cruise_speed_mps": item.cruise_speed_mps,
            "start_delay_seconds": item.start_delay_seconds, "revision": item.revision,
            "delivered_revision": item.delivered_revision,
            "edge_status": item.edge_status.value,
            "edge_revision": item.edge_revision,
            "edge_message": item.edge_message,
            "edge_updated_at": item.edge_updated_at.isoformat() if item.edge_updated_at else None,
            "waypoints": [waypoint.__dict__ for waypoint in item.waypoints],
        }

    @staticmethod
    def _parse_subtask(payload: dict[str, Any]) -> DeviceSubtask:
        from .task_models import EdgeTaskStatus
        updated_at = payload.get("edge_updated_at")
        try:
            edge_status = EdgeTaskStatus(str(payload.get("edge_status", EdgeTaskStatus.NO_TASK.value)))
        except ValueError:
            edge_status = EdgeTaskStatus.NO_TASK
        return DeviceSubtask(
            str(payload["subtask_id"]), str(payload["device_id"]), str(payload["device_name"]),
            str(payload["device_type"]), str(payload["ip_address"]), str(payload.get("layer_mode", "pointcloud")),
            tuple(TaskWaypoint(str(item["waypoint_id"]), float(item["x"]), float(item["y"]), float(item["z"])) for item in payload.get("waypoints", [])),
            float(payload.get("default_altitude_m", 1.0)), float(payload.get("cruise_speed_mps", 1.0)),
            float(payload.get("start_delay_seconds", 0.0)), int(payload.get("revision", 0)),
            int(payload["delivered_revision"]) if payload.get("delivered_revision") is not None else None,
            edge_status, int(payload["edge_revision"]) if payload.get("edge_revision") is not None else None,
            str(payload.get("edge_message", "")),
            datetime.fromisoformat(updated_at) if updated_at else None,
        )

    @staticmethod
    def _serialize_execution(item: TaskExecutionSnapshot) -> dict[str, Any]:
        return {
            "execution_id": item.execution_id, "task_id": item.task_id,
            "device_ids": list(item.device_ids), "status": item.status.value,
            "created_at": item.created_at.isoformat(),
            "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
            "updated_at": item.updated_at.isoformat(), "message": item.message,
            "device_states": [list(value) for value in item.device_states],
            "forced_conflict_reason": item.forced_conflict_reason,
        }

    @staticmethod
    def _parse_execution(payload: dict[str, Any]) -> TaskExecutionSnapshot:
        from .task_models import TaskExecutionStatus
        return TaskExecutionSnapshot(
            str(payload["execution_id"]), str(payload["task_id"]), tuple(map(str, payload["device_ids"])),
            TaskExecutionStatus(str(payload["status"])), datetime.fromisoformat(str(payload["created_at"])),
            datetime.fromisoformat(str(payload["scheduled_at"])) if payload.get("scheduled_at") else None,
            datetime.fromisoformat(str(payload["updated_at"])), str(payload.get("message", "")),
            tuple((str(item[0]), str(item[1])) for item in payload.get("device_states", [])),
            str(payload["forced_conflict_reason"]) if payload.get("forced_conflict_reason") else None,
        )

    def _atomic_json(self, path: Path, payload: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with self._write_lock:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
                ) as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    temporary = Path(handle.name)
                self._replace_with_retry(temporary, path)
        except OSError as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            raise TaskRepositoryError(f"任务文件写入失败：{exc}") from exc

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            with self._write_lock:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            event = self._event_from_payload(payload)
            cached = self._recent_events.get(event.task_id)
            if cached is not None:
                cached.append(event)
            self._notify(self.event_appended, event)
        except OSError as exc:
            raise TaskRepositoryError(f"任务日志写入失败：{exc}") from exc

    @staticmethod
    def _replace_with_retry(source: Path, target: Path) -> None:
        for attempt in range(5):
            try:
                os.replace(source, target)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))

    @staticmethod
    def _event_from_payload(payload: dict) -> TaskEvent:
        return TaskEvent(
            datetime.fromisoformat(str(payload["timestamp"])), str(payload["event_type"]),
            str(payload["message"]), TaskEventLevel(str(payload.get("level", "info"))),
            str(payload.get("task_id", "")), str(payload["execution_id"]) if payload.get("execution_id") else None,
            str(payload["device_id"]) if payload.get("device_id") else None, dict(payload.get("payload", {})),
        )

    @staticmethod
    def _tail_lines(path: Path, limit: int):
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            data = b""
            while position and data.count(b"\n") <= limit:
                size = min(position, 8192)
                position -= size
                handle.seek(position)
                data = handle.read(size) + data
            return data.splitlines()[-limit:]

    @staticmethod
    def _read_events(path: Path, limit: int | None = None) -> list[TaskEvent]:
        if not path.is_file():
            return []
        events = []
        try:
            lines = TaskRepository._tail_lines(path, limit) if limit else path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                events.append(TaskRepository._event_from_payload(json.loads(line)))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise TaskRepositoryError(f"任务日志读取失败：{exc}") from exc
        return events
