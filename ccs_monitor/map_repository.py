from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QObject, Signal
import yaml

from .models import (
    MapBounds,
    MapBuildingResultMetadata,
    MapCreatorDevice,
    MapDefinition,
    MapStatus,
    PgmMapMetadata,
)
from .pgm_map import PgmMapError, PgmMapLoader
from .point_cloud import MapPointCloudLoader, PointCloudError


MAP_SCHEMA_VERSION = 3
DEFAULT_MAP_ROOT = Path(__file__).resolve().parent.parent / "data" / "map_server"


class MapRepositoryError(RuntimeError):
    pass


class DuplicateMapNameError(MapRepositoryError):
    pass


def sanitize_map_name(name: str) -> str:
    normalized = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name.strip())
    normalized = re.sub(r"\s+", "_", normalized).strip(" ._")
    if not normalized:
        raise MapRepositoryError("地图名称不能为空或仅包含非法字符")
    return normalized[:80]


class MapRepository(QObject):
    maps_updated = Signal(object)

    def __init__(
        self,
        root: str | Path = DEFAULT_MAP_ROOT,
        loader: MapPointCloudLoader | None = None,
        pgm_loader: PgmMapLoader | None = None,
    ) -> None:
        super().__init__()
        self.root = Path(root)
        self.trash_root = self.root / ".trash"
        self.loader = loader or MapPointCloudLoader()
        self.pgm_loader = pgm_loader or PgmMapLoader()
        self._maps: list[MapDefinition] = []
        self.root.mkdir(parents=True, exist_ok=True)
        self.trash_root.mkdir(parents=True, exist_ok=True)
        self.load_all()

    def load_all(self) -> list[MapDefinition]:
        maps: list[MapDefinition] = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            metadata_path = directory / "map.json"
            try:
                maps.append(self._read_metadata(metadata_path, directory.name))
            except MapRepositoryError as exc:
                maps.append(self._error_definition(directory, str(exc)))
        maps.sort(key=lambda item: item.created_at, reverse=True)
        self._maps = maps
        return list(maps)

    def maps(self) -> list[MapDefinition]:
        return list(self._maps)

    def map_by_id(self, map_id: str) -> MapDefinition | None:
        return next((item for item in self._maps if item.map_id == map_id), None)

    def create(
        self,
        name: str,
        creator_devices: Iterable[MapCreatorDevice],
        frame_id: str = "map",
        *,
        now: datetime | None = None,
    ) -> MapDefinition:
        display_name = name.strip()
        safe_name = sanitize_map_name(display_name)
        devices = tuple(creator_devices)
        if not devices:
            raise MapRepositoryError("至少选择一台建图设备")
        self._ensure_unique_name(display_name)
        created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        timestamp = created_at.strftime("%Y%m%d_%H%M%S")
        directory_name = f"{safe_name}_{timestamp}"
        target = self.root / directory_name
        suffix = 2
        while target.exists():
            target = self.root / f"{directory_name}_{suffix}"
            suffix += 1
        target.mkdir(parents=False)
        definition = MapDefinition(
            map_id=str(uuid.uuid4()),
            name=display_name,
            frame_id=frame_id.strip() or "map",
            created_at=created_at,
            updated_at=created_at,
            creator_devices=devices,
            status=MapStatus.WAITING_FOR_PCD,
            directory_name=target.name,
        )
        try:
            self._write_metadata(definition)
        except Exception:
            target.rmdir()
            raise
        self._refresh_and_emit()
        return self.map_by_id(definition.map_id) or definition

    def rename(self, map_id: str, new_name: str) -> MapDefinition:
        current = self._require_map(map_id)
        display_name = new_name.strip()
        safe_name = sanitize_map_name(display_name)
        self._ensure_unique_name(display_name, excluding_id=map_id)
        timestamp = current.created_at.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = self.root / f"{safe_name}_{timestamp}"
        source = self.root / current.directory_name
        if target != source and target.exists():
            raise DuplicateMapNameError(f"地图目录已存在：{target.name}")
        updated = replace(current, name=display_name, updated_at=datetime.now(timezone.utc))
        if target != source:
            try:
                source.rename(target)
            except OSError as exc:
                raise MapRepositoryError(f"地图目录重命名失败：{exc}") from exc
            updated = replace(updated, directory_name=target.name)
        try:
            self._write_metadata(updated)
        except Exception:
            if target != source and target.exists() and not source.exists():
                target.rename(source)
            raise
        self._refresh_and_emit()
        return self.map_by_id(map_id) or updated

    def import_pcd(self, map_id: str, source_path: str | Path) -> MapDefinition:
        current = self._require_map(map_id)
        source = Path(source_path)
        if source.suffix.lower() != ".pcd":
            raise MapRepositoryError("请选择 .pcd 点云文件")
        directory = self.root / current.directory_name
        temporary = directory / ".map.importing.pcd"
        target = directory / "map.pcd"
        backup = directory / ".map.pcd.backup"
        try:
            shutil.copy2(source, temporary)
            data = self.loader.load(temporary)
            backup.unlink(missing_ok=True)
            if target.exists():
                os.replace(target, backup)
            os.replace(temporary, target)
        except (OSError, PointCloudError) as exc:
            temporary.unlink(missing_ok=True)
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise MapRepositoryError(f"点云导入失败：{exc}") from exc
        updated = replace(
            current,
            status=MapStatus.READY,
            pcd_path="map.pcd",
            point_count=data.point_count,
            bounds=data.bounds,
            width_m=data.bounds.width,
            height_m=data.bounds.height,
            updated_at=datetime.now(timezone.utc),
            error_message=None,
        )
        try:
            self._write_metadata(updated)
        except Exception:
            target.unlink(missing_ok=True)
            if backup.exists():
                os.replace(backup, target)
            raise
        backup.unlink(missing_ok=True)
        self._refresh_and_emit()
        return self.map_by_id(map_id) or updated

    def import_pgm(self, map_id: str, yaml_path: str | Path) -> MapDefinition:
        current = self._require_map(map_id)
        try:
            data = self.pgm_loader.load_yaml(yaml_path)
        except PgmMapError as exc:
            raise MapRepositoryError(f"栅格地图导入失败：{exc}") from exc
        directory = self.root / current.directory_name
        image_target = directory / "map.pgm"
        yaml_target = directory / "map.yaml"
        image_temporary = directory / ".map.importing.pgm"
        yaml_temporary = directory / ".map.importing.yaml"
        image_backup = directory / ".map.pgm.backup"
        yaml_backup = directory / ".map.yaml.backup"
        image_backed_up = False
        yaml_backed_up = False
        image_installed = False
        yaml_installed = False

        def restore_previous_layers() -> None:
            if image_installed:
                image_target.unlink(missing_ok=True)
            if yaml_installed:
                yaml_target.unlink(missing_ok=True)
            if image_backed_up:
                image_target.unlink(missing_ok=True)
                os.replace(image_backup, image_target)
            if yaml_backed_up:
                yaml_target.unlink(missing_ok=True)
                os.replace(yaml_backup, yaml_target)

        try:
            shutil.copy2(data.source_image_path, image_temporary)
            yaml_temporary.write_text(
                yaml.safe_dump(
                    self.pgm_loader.normalized_yaml(data.metadata),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            image_backup.unlink(missing_ok=True)
            yaml_backup.unlink(missing_ok=True)
            if image_target.exists():
                os.replace(image_target, image_backup)
                image_backed_up = True
            if yaml_target.exists():
                os.replace(yaml_target, yaml_backup)
                yaml_backed_up = True
            os.replace(image_temporary, image_target)
            image_installed = True
            os.replace(yaml_temporary, yaml_target)
            yaml_installed = True
        except (OSError, PgmMapError, yaml.YAMLError) as exc:
            image_temporary.unlink(missing_ok=True)
            yaml_temporary.unlink(missing_ok=True)
            try:
                restore_previous_layers()
            except OSError as restore_exc:
                raise MapRepositoryError(
                    f"栅格地图导入失败且旧图层恢复失败：{restore_exc}"
                ) from exc
            raise MapRepositoryError(f"栅格地图导入失败：{exc}") from exc
        metadata = replace(data.metadata, image_path="map.pgm", yaml_path="map.yaml")
        width = current.bounds.width if current.bounds else metadata.width_m
        height = current.bounds.height if current.bounds else metadata.height_m
        updated = replace(
            current,
            status=MapStatus.READY,
            pgm=metadata,
            width_m=width,
            height_m=height,
            updated_at=datetime.now(timezone.utc),
            error_message=None,
        )
        try:
            self._write_metadata(updated)
        except Exception:
            restore_previous_layers()
            raise
        image_backup.unlink(missing_ok=True)
        yaml_backup.unlink(missing_ok=True)
        self._refresh_and_emit()
        return self.map_by_id(map_id) or updated

    def delete(self, map_id: str) -> Path:
        current = self.map_by_id(map_id)
        if current is None:
            raise MapRepositoryError(f"地图不存在：{map_id}")
        source = self.root / current.directory_name
        target = self.trash_root / current.directory_name
        suffix = 2
        while target.exists():
            target = self.trash_root / f"{current.directory_name}_{suffix}"
            suffix += 1
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            raise MapRepositoryError(f"地图移入回收目录失败：{exc}") from exc
        self._refresh_and_emit()
        return target

    def export_zip(self, map_id: str, destination: str | Path) -> Path:
        current = self._require_map(map_id)
        source_dir = self.root / current.directory_name
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination_path.parent, prefix=f".{destination_path.name}.", suffix=".tmp", delete=False
            ) as handle:
                temporary_path = Path(handle.name)
            with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(source_dir / "map.json", "map.json")
                pcd_path = source_dir / "map.pcd"
                if pcd_path.is_file():
                    archive.write(pcd_path, "map.pcd")
                for filename in ("map.yaml", "map.pgm"):
                    layer_path = source_dir / filename
                    if layer_path.is_file():
                        archive.write(layer_path, filename)
                trajectory = source_dir / "trajectory.csv"
                if trajectory.is_file():
                    archive.write(trajectory, "trajectory.csv")
            os.replace(temporary_path, destination_path)
            return destination_path
        except OSError as exc:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
            raise MapRepositoryError(f"地图导出失败：{exc}") from exc

    def pcd_path(self, map_id: str) -> Path:
        current = self._require_map(map_id)
        if current.status != MapStatus.READY or not current.pcd_path:
            raise MapRepositoryError("该地图尚未导入有效点云")
        return self.root / current.directory_name / current.pcd_path

    def pgm_paths(self, map_id: str) -> tuple[Path, Path]:
        current = self._require_map(map_id)
        if current.pgm is None:
            raise MapRepositoryError("该地图尚未导入有效 PGM 栅格")
        directory = self.root / current.directory_name
        yaml_path = directory / current.pgm.yaml_path
        image_path = directory / current.pgm.image_path
        if not yaml_path.is_file() or not image_path.is_file():
            raise MapRepositoryError("PGM 栅格文件不完整")
        return yaml_path, image_path

    def mapping_session_directory(self, map_id: str, session_id: str, *, create: bool = False) -> Path:
        current = self._require_map(map_id)
        if not session_id or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", session_id):
            raise MapRepositoryError("建图会话 ID 无效")
        root = self.root / current.directory_name / ".mapping"
        path = root / session_id
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def write_mapping_checkpoint(
        self,
        map_id: str,
        session_id: str,
        session_payload: dict[str, Any],
        points: Any,
        trajectory_rows: Iterable[tuple[Any, ...]],
    ) -> Path:
        from .map_building import write_binary_pcd

        directory = self.mapping_session_directory(map_id, session_id, create=True)
        self._atomic_json(directory / "session.json", session_payload)
        pcd_temp = directory / ".partial.pcd.tmp"
        try:
            write_binary_pcd(pcd_temp, points)
            os.replace(pcd_temp, directory / "partial.pcd")
            self._write_trajectory_atomic(directory / "trajectory.csv", trajectory_rows)
        except (OSError, ValueError) as exc:
            pcd_temp.unlink(missing_ok=True)
            raise MapRepositoryError(f"建图检查点写入失败：{exc}") from exc
        return directory

    def interrupted_sessions(self, map_id: str) -> list[dict[str, Any]]:
        root = self.mapping_session_directory(map_id, "placeholder").parent
        sessions: list[dict[str, Any]] = []
        if not root.is_dir():
            return sessions
        for directory in sorted(root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            try:
                payload = json.loads((directory / "session.json").read_text(encoding="utf-8"))
                if payload.get("map_id") != map_id or payload.get("session_id") != directory.name:
                    raise ValueError("会话标识不一致")
                if not (directory / "partial.pcd").is_file():
                    raise ValueError("缺少 partial.pcd")
                sessions.append(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return sessions

    def discard_mapping_session(self, map_id: str, session_id: str) -> None:
        directory = self.mapping_session_directory(map_id, session_id)
        if directory.is_dir():
            shutil.rmtree(directory)

    def commit_mapping_result(
        self,
        map_id: str,
        session_id: str,
        metadata: MapBuildingResultMetadata,
    ) -> MapDefinition:
        current = self._require_map(map_id)
        session_dir = self.mapping_session_directory(map_id, session_id)
        partial = session_dir / "partial.pcd"
        trajectory = session_dir / "trajectory.csv"
        try:
            data = self.loader.load(partial)
        except (OSError, PointCloudError) as exc:
            raise MapRepositoryError(f"临时建图结果校验失败：{exc}") from exc
        directory = self.root / current.directory_name
        target_pcd = directory / "map.pcd"
        target_trajectory = directory / "trajectory.csv"
        backup_pcd = directory / ".map.pcd.mapping.backup"
        backup_trajectory = directory / ".trajectory.csv.mapping.backup"
        had_pcd = target_pcd.is_file()
        had_trajectory = target_trajectory.is_file()
        try:
            backup_pcd.unlink(missing_ok=True)
            backup_trajectory.unlink(missing_ok=True)
            if had_pcd:
                os.replace(target_pcd, backup_pcd)
            if had_trajectory:
                os.replace(target_trajectory, backup_trajectory)
            os.replace(partial, target_pcd)
            if trajectory.is_file():
                os.replace(trajectory, target_trajectory)
            updated = replace(
                current,
                status=MapStatus.READY,
                pcd_path="map.pcd",
                trajectory_path="trajectory.csv" if target_trajectory.is_file() else None,
                point_count=data.point_count,
                bounds=data.bounds,
                width_m=data.bounds.width,
                height_m=data.bounds.height,
                last_mapping=metadata,
                updated_at=datetime.now(timezone.utc),
                error_message=None,
            )
            self._write_metadata(updated)
        except Exception as exc:
            if target_pcd.exists():
                os.replace(target_pcd, partial)
            if target_trajectory.exists():
                os.replace(target_trajectory, trajectory)
            if had_pcd and backup_pcd.exists():
                os.replace(backup_pcd, target_pcd)
            if had_trajectory and backup_trajectory.exists():
                os.replace(backup_trajectory, target_trajectory)
            raise MapRepositoryError(f"建图结果提交失败：{exc}") from exc
        backup_pcd.unlink(missing_ok=True)
        backup_trajectory.unlink(missing_ok=True)
        shutil.rmtree(session_dir, ignore_errors=True)
        self._refresh_and_emit()
        return self.map_by_id(map_id) or updated

    def _require_map(self, map_id: str) -> MapDefinition:
        item = self.map_by_id(map_id)
        if item is None:
            raise MapRepositoryError(f"地图不存在：{map_id}")
        if item.status == MapStatus.ERROR:
            raise MapRepositoryError(item.error_message or "地图元数据损坏")
        return item

    def _ensure_unique_name(self, name: str, excluding_id: str | None = None) -> None:
        folded = name.strip().casefold()
        if any(item.map_id != excluding_id and item.name.casefold() == folded for item in self._maps):
            raise DuplicateMapNameError(f"地图名称已存在：{name}")

    def _refresh_and_emit(self) -> None:
        self.load_all()
        self.maps_updated.emit(self.maps())

    def _read_metadata(self, path: Path, directory_name: str) -> MapDefinition:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2, MAP_SCHEMA_VERSION}:
                raise ValueError("schema_version 不受支持")
            devices = tuple(
                MapCreatorDevice(str(item["device_id"]), str(item["device_name"]), str(item["device_type"]))
                for item in payload["creator_devices"]
            )
            bounds_payload = payload.get("bounds")
            bounds = MapBounds(**{key: float(bounds_payload[key]) for key in (
                "min_x", "min_y", "min_z", "max_x", "max_y", "max_z"
            )}) if bounds_payload else None
            pgm_payload = payload.get("pgm")
            pgm = PgmMapMetadata(
                image_path=str(pgm_payload["image_file"]),
                yaml_path=str(pgm_payload["yaml_file"]),
                resolution=float(pgm_payload["resolution"]),
                origin_x=float(pgm_payload["origin"][0]),
                origin_y=float(pgm_payload["origin"][1]),
                origin_yaw=float(pgm_payload["origin"][2]),
                image_width=int(pgm_payload["image_width"]),
                image_height=int(pgm_payload["image_height"]),
                negate=bool(pgm_payload["negate"]),
                occupied_thresh=float(pgm_payload["occupied_thresh"]),
                free_thresh=float(pgm_payload["free_thresh"]),
            ) if pgm_payload else None
            mapping_payload = payload.get("last_mapping")
            last_mapping = MapBuildingResultMetadata(
                session_id=str(mapping_payload["session_id"]),
                device_id=str(mapping_payload["device_id"]),
                started_at=datetime.fromisoformat(str(mapping_payload["started_at"])),
                ended_at=datetime.fromisoformat(str(mapping_payload["ended_at"])),
                protocol_id=str(mapping_payload["protocol_id"]),
                voxel_size_m=float(mapping_payload["voxel_size_m"]),
                complete_frames=int(mapping_payload["complete_frames"]),
                dropped_frames=int(mapping_payload["dropped_frames"]),
                received_points=int(mapping_payload["received_points"]),
                fused_points=int(mapping_payload["fused_points"]),
            ) if mapping_payload else None
            if pgm is not None:
                pgm_numbers = (
                    pgm.resolution,
                    pgm.origin_x,
                    pgm.origin_y,
                    pgm.origin_yaw,
                    pgm.occupied_thresh,
                    pgm.free_thresh,
                )
                if not all(math.isfinite(value) for value in pgm_numbers):
                    raise ValueError("PGM 元数据数值必须为有限数")
                if pgm.resolution <= 0 or pgm.image_width <= 0 or pgm.image_height <= 0:
                    raise ValueError("PGM 分辨率和图像尺寸必须大于零")
                if not 0 <= pgm.free_thresh < pgm.occupied_thresh <= 1:
                    raise ValueError("PGM 占据阈值无效")
            definition = MapDefinition(
                map_id=str(payload["map_id"]),
                name=str(payload["name"]),
                frame_id=str(payload.get("frame_id", "map")),
                width_m=bounds.width if bounds else 0.0,
                height_m=bounds.height if bounds else 0.0,
                created_at=datetime.fromisoformat(str(payload["created_at"])),
                updated_at=datetime.fromisoformat(str(payload["updated_at"])),
                creator_devices=devices,
                status=MapStatus(str(payload["status"])),
                pcd_path=str(payload["pcd_file"]) if payload.get("pcd_file") else None,
                point_count=int(payload.get("point_count", 0)),
                bounds=bounds,
                pgm=pgm,
                last_mapping=last_mapping,
                trajectory_path=str(payload["trajectory_file"]) if payload.get("trajectory_file") else None,
                directory_name=directory_name,
            )
            if not definition.map_id or not definition.name or not devices:
                raise ValueError("地图 ID、名称和建图设备不能为空")
            if definition.point_count < 0:
                raise ValueError("point_count 不能为负数")
            if last_mapping is not None:
                if not last_mapping.session_id or not last_mapping.device_id or not last_mapping.protocol_id:
                    raise ValueError("最近建图会话标识不能为空")
                if not math.isfinite(last_mapping.voxel_size_m) or last_mapping.voxel_size_m <= 0:
                    raise ValueError("最近建图体素尺寸无效")
                if min(
                    last_mapping.complete_frames,
                    last_mapping.dropped_frames,
                    last_mapping.received_points,
                    last_mapping.fused_points,
                ) < 0:
                    raise ValueError("最近建图统计不能为负数")
            pcd_valid = bool(definition.pcd_path and (path.parent / definition.pcd_path).is_file())
            pgm_valid = bool(
                definition.pgm
                and (path.parent / definition.pgm.image_path).is_file()
                and (path.parent / definition.pgm.yaml_path).is_file()
            )
            if definition.status == MapStatus.READY and not (pcd_valid or pgm_valid):
                raise ValueError("元数据标记为就绪，但不存在有效 PCD 或 PGM 图层")
            if definition.trajectory_path and not (path.parent / definition.trajectory_path).is_file():
                raise ValueError("轨迹文件不存在")
            return definition
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise MapRepositoryError(f"地图元数据无效：{exc}") from exc

    def _write_metadata(self, definition: MapDefinition) -> None:
        directory = self.root / definition.directory_name
        payload: dict[str, Any] = {
            "schema_version": MAP_SCHEMA_VERSION,
            "map_id": definition.map_id,
            "name": definition.name,
            "frame_id": definition.frame_id,
            "created_at": definition.created_at.isoformat(),
            "updated_at": definition.updated_at.isoformat(),
            "creator_devices": [
                {"device_id": item.device_id, "device_name": item.device_name, "device_type": item.device_type}
                for item in definition.creator_devices
            ],
            "status": definition.status.value,
            "pcd_file": definition.pcd_path,
            "point_count": definition.point_count,
            "bounds": definition.bounds.__dict__ if definition.bounds else None,
            "pgm": self._serialize_pgm(definition.pgm),
            "trajectory_file": definition.trajectory_path,
            "last_mapping": self._serialize_mapping(definition.last_mapping),
        }
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory, prefix=".map.json.", suffix=".tmp", delete=False
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, directory / "map.json")
        except OSError as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            raise MapRepositoryError(f"地图元数据写入失败：{exc}") from exc

    @staticmethod
    def _serialize_pgm(metadata: PgmMapMetadata | None) -> dict[str, Any] | None:
        if metadata is None:
            return None
        return {
            "image_file": metadata.image_path,
            "yaml_file": metadata.yaml_path,
            "resolution": metadata.resolution,
            "origin": [metadata.origin_x, metadata.origin_y, metadata.origin_yaw],
            "image_width": metadata.image_width,
            "image_height": metadata.image_height,
            "negate": metadata.negate,
            "occupied_thresh": metadata.occupied_thresh,
            "free_thresh": metadata.free_thresh,
        }

    @staticmethod
    def _serialize_mapping(metadata: MapBuildingResultMetadata | None) -> dict[str, Any] | None:
        if metadata is None:
            return None
        return {
            "session_id": metadata.session_id,
            "device_id": metadata.device_id,
            "started_at": metadata.started_at.isoformat(),
            "ended_at": metadata.ended_at.isoformat(),
            "protocol_id": metadata.protocol_id,
            "voxel_size_m": metadata.voxel_size_m,
            "complete_frames": metadata.complete_frames,
            "dropped_frames": metadata.dropped_frames,
            "received_points": metadata.received_points,
            "fused_points": metadata.fused_points,
        }

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise MapRepositoryError(f"JSON 原子写入失败：{exc}") from exc

    @staticmethod
    def _write_trajectory_atomic(path: Path, rows: Iterable[tuple[Any, ...]]) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                handle.write("sample_stamp_ns,x,y,z,qx,qy,qz,qw\n")
                for row in rows:
                    handle.write(",".join(str(value) for value in row) + "\n")
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _error_definition(directory: Path, message: str) -> MapDefinition:
        timestamp = datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc)
        return MapDefinition(
            map_id=f"error:{directory.name}",
            name=directory.name,
            created_at=timestamp,
            updated_at=timestamp,
            status=MapStatus.ERROR,
            directory_name=directory.name,
            error_message=message,
        )
