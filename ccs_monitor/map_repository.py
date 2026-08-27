from __future__ import annotations

import json
import hashlib
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
    MapBuildMode,
    MapBuildProvenance,
    MapBuildingResultMetadata,
    MapCreatorDevice,
    MapDefinition,
    MapStatus,
    MapTransform,
    PgmFusionProvenance,
    PgmFusionSource,
    PgmMapMetadata,
    PgmTransform2D,
    DeviceProfile,
)
from .pgm_map import PcdToPgmGenerator, PcdToPgmOptions, PgmMapError, PgmMapLoader
from .pgm_fusion import pcd_sha256
from .point_cloud import MapPointCloudLoader, PointCloudError


MAP_SCHEMA_VERSION = 5
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
    active_map_changed = Signal(object)

    def __init__(
        self,
        root: str | Path = DEFAULT_MAP_ROOT,
        loader: MapPointCloudLoader | None = None,
        pgm_loader: PgmMapLoader | None = None,
        pgm_generator: PcdToPgmGenerator | None = None,
    ) -> None:
        super().__init__()
        self.root = Path(root)
        self.trash_root = self.root / ".trash"
        self.fusion_root = self.root / ".fusion"
        self.loader = loader or MapPointCloudLoader()
        self.pgm_loader = pgm_loader or PgmMapLoader()
        self.pgm_generator = pgm_generator or PcdToPgmGenerator(self.loader)
        self._maps: list[MapDefinition] = []
        self.root.mkdir(parents=True, exist_ok=True)
        self.trash_root.mkdir(parents=True, exist_ok=True)
        self.fusion_root.mkdir(parents=True, exist_ok=True)
        self.active_map_file = self.root / "active_map.json"
        self._active_map_id: str | None = None
        self.load_all()
        self._load_active_map()

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

    def active_map_id(self) -> str | None:
        return self._active_map_id

    def active_map(self) -> MapDefinition | None:
        return self.map_by_id(self._active_map_id) if self._active_map_id else None

    def set_active_map(self, map_id: str) -> MapDefinition:
        definition = self.map_by_id(map_id)
        if definition is None or definition.status != MapStatus.READY or not (definition.pcd_path or definition.pgm):
            raise MapRepositoryError("只能激活包含有效 PCD 或 PGM 图层的就绪地图")
        self._active_map_id = definition.map_id
        self._atomic_json(self.active_map_file, {"map_id": definition.map_id})
        self.active_map_changed.emit(definition)
        return definition

    def _load_active_map(self) -> None:
        active_id = None
        try:
            payload = json.loads(self.active_map_file.read_text(encoding="utf-8"))
            active_id = payload.get("map_id") if isinstance(payload, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        candidate = self.map_by_id(str(active_id)) if active_id else None
        if candidate is None or candidate.status != MapStatus.READY or not (candidate.pcd_path or candidate.pgm):
            candidate = next((item for item in self._maps if item.status == MapStatus.READY and (item.pcd_path or item.pgm)), None)
        self._active_map_id = candidate.map_id if candidate else None
        if self._active_map_id:
            self._atomic_json(self.active_map_file, {"map_id": self._active_map_id})
        else:
            self.active_map_file.unlink(missing_ok=True)

    def update_device_reference(
        self, original_device_id: str, profile: DeviceProfile
    ) -> tuple[MapDefinition, ...]:
        folded = original_device_id.casefold()
        originals: list[MapDefinition] = []
        updated_items: list[MapDefinition] = []
        for definition in self._maps:
            if definition.error_message or not any(
                item.device_id.casefold() == folded for item in definition.creator_devices
            ):
                continue
            originals.append(definition)
            creators = tuple(
                MapCreatorDevice(profile.device_id, profile.device_name, profile.device_type)
                if item.device_id.casefold() == folded else item
                for item in definition.creator_devices
            )
            updated_items.append(replace(definition, creator_devices=creators))
        try:
            for definition in updated_items:
                self._write_metadata(definition)
        except Exception:
            for definition in originals:
                self._write_metadata(definition)
            raise
        if updated_items:
            self._refresh_and_emit()
        return tuple(originals)

    def restore_definitions(self, definitions: Iterable[MapDefinition]) -> None:
        restored = tuple(definitions)
        for definition in restored:
            self._write_metadata(definition)
        if restored:
            self._refresh_and_emit()

    def create(
        self,
        name: str,
        creator_devices: Iterable[MapCreatorDevice],
        frame_id: str = "map",
        *,
        now: datetime | None = None,
        allow_empty_devices: bool = False,
        build_provenance: MapBuildProvenance | None = None,
    ) -> MapDefinition:
        display_name = name.strip()
        safe_name = sanitize_map_name(display_name)
        devices = tuple(creator_devices)
        if not devices and not allow_empty_devices:
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
            build_provenance=build_provenance,
        )
        try:
            self._write_metadata(definition)
        except Exception:
            target.rmdir()
            raise
        self._refresh_and_emit()
        return self.map_by_id(definition.map_id) or definition

    def create_empty(
        self,
        name: str,
        frame_id: str = "map",
        *,
        now: datetime | None = None,
    ) -> MapDefinition:
        provenance = MapBuildProvenance(MapBuildMode.EMPTY, uuid.uuid4().hex)
        return self.create(
            name, (), frame_id, now=now, allow_empty_devices=True,
            build_provenance=provenance,
        )

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

    def generate_pgm(
        self, map_id: str, options: PcdToPgmOptions | None = None
    ) -> MapDefinition:
        current = self._require_map(map_id)
        if not current.pcd_path:
            raise MapRepositoryError("该地图没有可用于生成 PGM 的点云")
        pcd_path = self.root / current.directory_name / current.pcd_path
        if not pcd_path.is_file():
            raise MapRepositoryError("地图点云文件不存在")
        try:
            with tempfile.TemporaryDirectory(prefix="ccs-pgm-generation-") as directory:
                temporary_root = Path(directory)
                self.pgm_generator.generate(
                    pcd_path,
                    temporary_root / "map.pgm",
                    temporary_root / "map.yaml",
                    options,
                )
                return self.import_pgm(map_id, temporary_root / "map.yaml")
        except PgmMapError as exc:
            raise MapRepositoryError(f"PGM 生成失败：{exc}") from exc

    def pcd_fingerprint(self, map_id: str) -> str:
        current = self._require_map(map_id)
        if not current.pcd_path:
            raise MapRepositoryError("目标地图没有有效 PCD")
        path = self.root / current.directory_name / current.pcd_path
        if not path.is_file():
            raise MapRepositoryError("目标地图 PCD 文件不存在")
        try:
            return pcd_sha256(path)
        except OSError as exc:
            raise MapRepositoryError(f"PCD 指纹计算失败：{exc}") from exc

    def pgm_fusion_job_directory(self, map_id: str, job_id: str, *, create: bool = True) -> Path:
        current = self._require_map(map_id)
        safe_job_id = re.sub(r"[^A-Za-z0-9_-]", "", job_id)
        if not safe_job_id or safe_job_id != job_id:
            raise MapRepositoryError("PGM 融合任务 ID 无效")
        root = self.root / current.directory_name / ".pgm_fusion" / safe_job_id
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    def write_pgm_fusion_job(self, map_id: str, job_id: str, payload: dict[str, Any]) -> Path:
        root = self.pgm_fusion_job_directory(map_id, job_id)
        self._atomic_json(root / "job.json", payload)
        return root

    def interrupted_pgm_fusion_jobs(self, map_id: str) -> list[dict[str, Any]]:
        current = self._require_map(map_id)
        root = self.root / current.directory_name / ".pgm_fusion"
        results: list[dict[str, Any]] = []
        if not root.is_dir():
            return results
        for job_file in sorted(root.glob("*/job.json")):
            try:
                payload = json.loads(job_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("state") != "completed":
                    results.append(payload)
            except (OSError, json.JSONDecodeError):
                continue
        return results

    def discard_pgm_fusion_job(self, map_id: str, job_id: str) -> Path:
        source = self.pgm_fusion_job_directory(map_id, job_id, create=False)
        if not source.is_dir():
            raise MapRepositoryError("PGM 融合临时任务不存在")
        trash = self.root / self._require_map(map_id).directory_name / ".pgm_fusion" / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / job_id
        suffix = 2
        while target.exists():
            target = trash / f"{job_id}_{suffix}"
            suffix += 1
        shutil.move(str(source), str(target))
        return target

    def commit_pgm_fusion_result(
        self, map_id: str, job_id: str, yaml_path: str | Path,
        provenance: PgmFusionProvenance,
    ) -> MapDefinition:
        current = self._require_map(map_id)
        if self.pcd_fingerprint(map_id) != provenance.target_pcd_sha256:
            raise MapRepositoryError("目标 PCD 已变化，必须重新执行 PGM 融合")
        try:
            data = self.pgm_loader.load_yaml(yaml_path)
        except PgmMapError as exc:
            raise MapRepositoryError(f"融合 PGM 校验失败：{exc}") from exc
        directory = self.root / current.directory_name
        image_target, yaml_target = directory / "map.pgm", directory / "map.yaml"
        image_temporary = directory / ".map.fusion.pgm"
        yaml_temporary = directory / ".map.fusion.yaml"
        image_backup, yaml_backup = directory / ".map.pgm.backup", directory / ".map.yaml.backup"
        backed_image = backed_yaml = installed_image = installed_yaml = False

        def restore() -> None:
            if installed_image:
                image_target.unlink(missing_ok=True)
            if installed_yaml:
                yaml_target.unlink(missing_ok=True)
            if backed_image:
                os.replace(image_backup, image_target)
            if backed_yaml:
                os.replace(yaml_backup, yaml_target)

        try:
            shutil.copy2(data.source_image_path, image_temporary)
            normalized = replace(data.metadata, image_path="map.pgm", yaml_path="map.yaml")
            yaml_temporary.write_text(
                yaml.safe_dump(self.pgm_loader.normalized_yaml(normalized), allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            image_backup.unlink(missing_ok=True)
            yaml_backup.unlink(missing_ok=True)
            if image_target.exists():
                os.replace(image_target, image_backup)
                backed_image = True
            if yaml_target.exists():
                os.replace(yaml_target, yaml_backup)
                backed_yaml = True
            os.replace(image_temporary, image_target)
            installed_image = True
            os.replace(yaml_temporary, yaml_target)
            installed_yaml = True
            updated = replace(
                current, pgm=normalized, pgm_fusion=provenance,
                status=MapStatus.READY, updated_at=datetime.now(timezone.utc), error_message=None,
            )
            self._write_metadata(updated)
        except Exception as exc:
            image_temporary.unlink(missing_ok=True)
            yaml_temporary.unlink(missing_ok=True)
            try:
                restore()
            except OSError as restore_exc:
                raise MapRepositoryError(f"PGM 融合提交失败且旧图层恢复失败：{restore_exc}") from exc
            if isinstance(exc, MapRepositoryError):
                raise
            raise MapRepositoryError(f"PGM 融合提交失败：{exc}") from exc
        image_backup.unlink(missing_ok=True)
        yaml_backup.unlink(missing_ok=True)
        job_root = self.pgm_fusion_job_directory(map_id, job_id, create=False)
        if (job_root / "job.json").is_file():
            try:
                payload = json.loads((job_root / "job.json").read_text(encoding="utf-8"))
                payload["state"] = "completed"
                payload["completed_at"] = datetime.now(timezone.utc).isoformat()
                self._atomic_json(job_root / "job.json", payload)
            except (OSError, json.JSONDecodeError):
                pass
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
        if self._active_map_id == map_id:
            self._active_map_id = None
        self._refresh_and_emit()
        self._load_active_map()
        self.active_map_changed.emit(self.active_map())
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
                trajectories = source_dir / "trajectories"
                if trajectories.is_dir():
                    for trajectory_file in sorted(trajectories.glob("*.csv")):
                        archive.write(trajectory_file, f"trajectories/{trajectory_file.name}")
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

    def fusion_job_directory(self, job_id: str, *, create: bool = False) -> Path:
        if not job_id or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", job_id):
            raise MapRepositoryError("融合任务 ID 无效")
        path = self.fusion_root / job_id
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def interrupted_fusion_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        if not self.fusion_root.is_dir():
            return jobs
        for directory in sorted(self.fusion_root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            try:
                payload = json.loads((directory / "job.json").read_text(encoding="utf-8"))
                if payload.get("job_id") != directory.name:
                    raise ValueError("融合任务标识不一致")
                jobs.append(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return jobs

    def write_fusion_job(self, job_id: str, payload: dict[str, Any]) -> Path:
        directory = self.fusion_job_directory(job_id, create=True)
        self._atomic_json(directory / "job.json", payload)
        return directory

    def commit_fusion_result(
        self,
        name: str,
        job_id: str,
        output_pcd: str | Path,
        creator_devices: Iterable[MapCreatorDevice],
        frame_id: str,
        provenance: MapBuildProvenance,
        *,
        now: datetime | None = None,
        output_pgm_yaml: str | Path | None = None,
        pgm_provenance: PgmFusionProvenance | None = None,
    ) -> MapDefinition:
        source = Path(output_pcd)
        try:
            cloud = self.loader.load(source)
        except (OSError, PointCloudError) as exc:
            raise MapRepositoryError(f"融合结果校验失败：{exc}") from exc
        pgm_data = None
        if output_pgm_yaml is not None:
            try:
                pgm_data = self.pgm_loader.load_yaml(output_pgm_yaml)
            except PgmMapError as exc:
                raise MapRepositoryError(f"同步融合 PGM 校验失败：{exc}") from exc
            if pgm_provenance is None:
                raise MapRepositoryError("同步融合 PGM 缺少来源元数据")
            try:
                output_fingerprint = pcd_sha256(source)
            except OSError as exc:
                raise MapRepositoryError(f"融合 PCD 指纹计算失败：{exc}") from exc
            if pgm_provenance.target_pcd_sha256 != output_fingerprint:
                raise MapRepositoryError("同步融合 PGM 绑定的 PCD 指纹不匹配")
        self._ensure_unique_name(name)
        definition = self.create(
            name, tuple(creator_devices), frame_id, now=now,
            allow_empty_devices=True, build_provenance=provenance,
        )
        directory = self.root / definition.directory_name
        target = directory / "map.pcd"
        try:
            temporary = directory / ".fusion-result.pcd"
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            pgm_metadata = None
            if pgm_data is not None:
                image_temporary = directory / ".fusion-result.pgm"
                yaml_temporary = directory / ".fusion-result.yaml"
                shutil.copy2(pgm_data.source_image_path, image_temporary)
                pgm_metadata = replace(
                    pgm_data.metadata, image_path="map.pgm", yaml_path="map.yaml"
                )
                yaml_temporary.write_text(
                    yaml.safe_dump(
                        self.pgm_loader.normalized_yaml(pgm_metadata),
                        allow_unicode=True, sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                os.replace(image_temporary, directory / "map.pgm")
                os.replace(yaml_temporary, directory / "map.yaml")
            updated = replace(
                definition, status=MapStatus.READY, pcd_path="map.pcd",
                point_count=cloud.point_count, bounds=cloud.bounds,
                width_m=cloud.bounds.width, height_m=cloud.bounds.height,
                pgm=pgm_metadata, pgm_fusion=pgm_provenance,
                updated_at=datetime.now(timezone.utc), build_provenance=provenance,
            )
            self._write_metadata(updated)
        except Exception as exc:
            shutil.rmtree(directory, ignore_errors=True)
            self._refresh_and_emit()
            raise MapRepositoryError(f"融合地图提交失败：{exc}") from exc
        shutil.rmtree(self.fusion_job_directory(job_id), ignore_errors=True)
        self._refresh_and_emit()
        return self.map_by_id(updated.map_id) or updated

    def commit_fusion_to_existing(
        self,
        map_id: str,
        job_id: str,
        output_pcd: str | Path,
        provenance: MapBuildProvenance,
        last_mapping: MapBuildingResultMetadata | None = None,
    ) -> MapDefinition:
        current = self._require_map(map_id)
        source = Path(output_pcd)
        try:
            cloud = self.loader.load(source)
        except (OSError, PointCloudError) as exc:
            raise MapRepositoryError(f"联合建图结果校验失败：{exc}") from exc
        directory = self.root / current.directory_name
        target = directory / "map.pcd"
        temporary = directory / ".map.fusion.tmp"
        backup = directory / ".map.fusion.backup"
        had_target = target.is_file()
        source_job_dir = self.mapping_session_directory(map_id, job_id)
        trajectories_target = directory / "trajectories"
        trajectory_file: str | None = current.trajectory_path
        try:
            shutil.copy2(source, temporary)
            backup.unlink(missing_ok=True)
            if had_target:
                os.replace(target, backup)
            os.replace(temporary, target)
            device_trajectories = [
                path for path in source_job_dir.glob("*/trajectory.csv") if path.is_file()
            ]
            if len(device_trajectories) == 1:
                root_trajectory = directory / "trajectory.csv"
                shutil.copy2(device_trajectories[0], root_trajectory)
                trajectory_file = "trajectory.csv"
            elif device_trajectories:
                trajectories_target.mkdir(exist_ok=True)
                for path in device_trajectories:
                    shutil.copy2(path, trajectories_target / f"{path.parent.name}.csv")
                trajectory_file = None
            updated = replace(
                current, status=MapStatus.READY, pcd_path="map.pcd",
                point_count=cloud.point_count, bounds=cloud.bounds,
                width_m=cloud.bounds.width, height_m=cloud.bounds.height,
                updated_at=datetime.now(timezone.utc), build_provenance=provenance,
                last_mapping=last_mapping, trajectory_path=trajectory_file,
                error_message=None,
            )
            self._write_metadata(updated)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if target.is_file():
                target.unlink(missing_ok=True)
            if had_target and backup.is_file():
                os.replace(backup, target)
            raise MapRepositoryError(f"联合建图结果提交失败：{exc}") from exc
        backup.unlink(missing_ok=True)
        shutil.rmtree(self.mapping_session_directory(map_id, job_id), ignore_errors=True)
        self._refresh_and_emit()
        return self.map_by_id(map_id) or updated

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

    def write_mapping_job_checkpoint(
        self,
        map_id: str,
        job_id: str,
        job_payload: dict[str, Any],
        device_id: str,
        points: Any,
        trajectory_rows: Iterable[tuple[Any, ...]],
    ) -> Path:
        from .map_building import write_binary_pcd

        root = self.mapping_session_directory(map_id, job_id, create=True)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", device_id):
            raise MapRepositoryError("建图设备 ID 无效")
        device_dir = root / device_id
        device_dir.mkdir(exist_ok=True)
        self._atomic_json(root / "job.json", job_payload)
        temporary = device_dir / ".partial.pcd.tmp"
        try:
            write_binary_pcd(temporary, points)
            os.replace(temporary, device_dir / "partial.pcd")
            self._write_trajectory_atomic(device_dir / "trajectory.csv", trajectory_rows)
        except (OSError, ValueError) as exc:
            temporary.unlink(missing_ok=True)
            raise MapRepositoryError(f"联合建图检查点写入失败：{exc}") from exc
        return device_dir

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

    def interrupted_mapping_jobs(self, map_id: str) -> list[dict[str, Any]]:
        root = self.mapping_session_directory(map_id, "placeholder").parent
        jobs: list[dict[str, Any]] = []
        if not root.is_dir():
            return jobs
        for directory in sorted(root.iterdir(), reverse=True):
            if not directory.is_dir() or not (directory / "job.json").is_file():
                continue
            try:
                payload = json.loads((directory / "job.json").read_text(encoding="utf-8"))
                if payload.get("map_id") != map_id or payload.get("job_id") != directory.name:
                    raise ValueError("联合建图任务标识不一致")
                if not any(directory.glob("*/partial.pcd")):
                    raise ValueError("联合建图任务缺少设备点云")
                jobs.append(payload)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return jobs

    def discard_mapping_job(self, map_id: str, job_id: str) -> None:
        directory = self.mapping_session_directory(map_id, job_id)
        if directory.is_dir():
            shutil.rmtree(directory)

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

    def commit_remote_mapping_artifact(
        self,
        map_id: str,
        artifact,
        metadata: MapBuildingResultMetadata,
    ) -> MapDefinition:
        """Atomically replace all authoritative map layers from a validated v2 artifact."""
        current = self._require_map(map_id)
        try:
            cloud = self.loader.load(artifact.pcd_path)
            pgm_data = self.pgm_loader.load_yaml(artifact.yaml_path)
        except (OSError, PointCloudError, PgmMapError) as exc:
            raise MapRepositoryError(f"遥控建图成果校验失败：{exc}") from exc
        if artifact.frame_id != current.frame_id:
            raise MapRepositoryError(
                f"成果坐标系 {artifact.frame_id} 与地图坐标系 {current.frame_id} 不一致"
            )
        directory = self.root / current.directory_name
        pgm_metadata = replace(
            pgm_data.metadata, image_path="map.pgm", yaml_path="map.yaml"
        )
        normalized_yaml = yaml.safe_dump(
            self.pgm_loader.normalized_yaml(pgm_metadata),
            allow_unicode=True, sort_keys=False,
        )
        committed_metadata = replace(
            metadata,
            yaml_sha256=hashlib.sha256(normalized_yaml.encode("utf-8")).hexdigest(),
        )
        sources = {
            "map.pcd": Path(artifact.pcd_path),
            "map.pgm": Path(artifact.pgm_path),
        }
        installed: list[Path] = []
        backups: dict[Path, Path] = {}
        temporaries: dict[Path, Path] = {}
        trajectory = directory / "trajectory.csv"
        trajectory_backup = directory / ".trajectory.csv.remote.backup"
        try:
            for filename, source in sources.items():
                target = directory / filename
                temporary = directory / f".{filename}.remote.tmp"
                backup = directory / f".{filename}.remote.backup"
                temporary.unlink(missing_ok=True)
                backup.unlink(missing_ok=True)
                shutil.copy2(source, temporary)
                temporaries[target] = temporary
                if target.is_file():
                    os.replace(target, backup)
                    backups[target] = backup
                os.replace(temporary, target)
                installed.append(target)
            yaml_target = directory / "map.yaml"
            yaml_temporary = directory / ".map.yaml.remote.tmp"
            yaml_backup = directory / ".map.yaml.remote.backup"
            yaml_temporary.unlink(missing_ok=True)
            yaml_backup.unlink(missing_ok=True)
            yaml_temporary.write_bytes(normalized_yaml.encode("utf-8"))
            temporaries[yaml_target] = yaml_temporary
            if yaml_target.is_file():
                os.replace(yaml_target, yaml_backup)
                backups[yaml_target] = yaml_backup
            os.replace(yaml_temporary, yaml_target)
            installed.append(yaml_target)
            trajectory_backup.unlink(missing_ok=True)
            if trajectory.is_file():
                os.replace(trajectory, trajectory_backup)
            updated = replace(
                current,
                status=MapStatus.READY,
                frame_id=artifact.frame_id,
                pcd_path="map.pcd",
                point_count=cloud.point_count,
                bounds=cloud.bounds,
                width_m=cloud.bounds.width,
                height_m=cloud.bounds.height,
                pgm=pgm_metadata,
                trajectory_path=None,
                last_mapping=committed_metadata,
                updated_at=datetime.now(timezone.utc),
                error_message=None,
            )
            self._write_metadata(updated)
        except Exception as exc:
            for temporary in temporaries.values():
                temporary.unlink(missing_ok=True)
            for target in reversed(installed):
                target.unlink(missing_ok=True)
                backup = backups.get(target)
                if backup and backup.is_file():
                    os.replace(backup, target)
            if trajectory_backup.is_file():
                os.replace(trajectory_backup, trajectory)
            raise MapRepositoryError(f"遥控建图成果提交失败：{exc}") from exc
        for backup in backups.values():
            backup.unlink(missing_ok=True)
        trajectory_backup.unlink(missing_ok=True)
        shutil.rmtree(self.mapping_session_directory(map_id, metadata.session_id), ignore_errors=True)
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
        if self._active_map_id:
            active = self.map_by_id(self._active_map_id)
            if active is None or active.status != MapStatus.READY or not (active.pcd_path or active.pgm):
                self._active_map_id = None
                self._load_active_map()
        self.maps_updated.emit(self.maps())

    def _read_metadata(self, path: Path, directory_name: str) -> MapDefinition:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
            if schema_version not in {1, 2, 3, 4, MAP_SCHEMA_VERSION}:
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
                sample_window_seconds=(
                    float(mapping_payload["sample_window_seconds"])
                    if mapping_payload.get("sample_window_seconds") is not None else None
                ),
                artifact_sha256=str(mapping_payload["artifact_sha256"])
                if mapping_payload.get("artifact_sha256") else None,
                pcd_sha256=str(mapping_payload["pcd_sha256"])
                if mapping_payload.get("pcd_sha256") else None,
                pgm_sha256=str(mapping_payload["pgm_sha256"])
                if mapping_payload.get("pgm_sha256") else None,
                yaml_sha256=str(mapping_payload["yaml_sha256"])
                if mapping_payload.get("yaml_sha256") else None,
            ) if mapping_payload else None
            provenance = self._parse_provenance(payload.get("build_provenance"))
            pgm_fusion = self._parse_pgm_fusion(payload.get("pgm_fusion"))
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
                build_provenance=provenance,
                pgm_fusion=pgm_fusion,
            )
            if not definition.map_id or not definition.name:
                raise ValueError("地图 ID 和名称不能为空")
            if not devices and schema_version < 4:
                raise ValueError("旧版地图的建图设备不能为空")
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
            "build_provenance": self._serialize_provenance(definition.build_provenance),
            "pgm_fusion": self._serialize_pgm_fusion(definition.pgm_fusion),
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
            "sample_window_seconds": metadata.sample_window_seconds,
            "artifact_sha256": metadata.artifact_sha256,
            "pcd_sha256": metadata.pcd_sha256,
            "pgm_sha256": metadata.pgm_sha256,
            "yaml_sha256": metadata.yaml_sha256,
        }

    @staticmethod
    def _serialize_provenance(metadata: MapBuildProvenance | None) -> dict[str, Any] | None:
        if metadata is None:
            return None
        return {
            "mode": metadata.mode.value,
            "job_id": metadata.job_id,
            "primary_source_id": metadata.primary_source_id,
            "source_ids": list(metadata.source_ids),
            "transforms": [
                {
                    "source_id": item.source_id,
                    "is_primary": item.is_primary,
                    "translation_m": list(item.translation_m),
                    "rotation_rpy_deg": list(item.rotation_rpy_deg),
                }
                for item in metadata.transforms
            ],
            "algorithm_id": metadata.algorithm_id,
            "algorithm_version": metadata.algorithm_version,
            "algorithm_sha256": metadata.algorithm_sha256,
            "excluded_device_ids": list(metadata.excluded_device_ids),
            "started_at": metadata.started_at.isoformat() if metadata.started_at else None,
            "ended_at": metadata.ended_at.isoformat() if metadata.ended_at else None,
        }

    @staticmethod
    def _serialize_pgm_fusion(metadata: PgmFusionProvenance | None) -> dict[str, Any] | None:
        if metadata is None:
            return None
        return {
            "job_id": metadata.job_id,
            "target_pcd_sha256": metadata.target_pcd_sha256,
            "sources": [
                {
                    "source_id": item.source_id,
                    "source_map_id": item.source_map_id,
                    "source_frame_id": item.source_frame_id or (
                        item.manifest.frame_id if item.manifest else None
                    ),
                    "device_id": item.device_id,
                    "device_name": item.device_name,
                    "existing_target_layer": item.existing_target_layer,
                    "transform": {
                        "x_m": item.transform.x_m,
                        "y_m": item.transform.y_m,
                        "yaw_deg": item.transform.yaw_deg,
                    },
                    "artifact_sha256": item.artifact_sha256 or (item.manifest.sha256 if item.manifest else None),
                    "frame_id": item.manifest.frame_id if item.manifest else None,
                }
                for item in metadata.sources
            ],
            "output_resolution": metadata.output_resolution,
            "merge_policy": metadata.merge_policy,
            "clipped_cells": metadata.clipped_cells,
            "clipped_area_m2": metadata.clipped_area_m2,
            "created_at": metadata.created_at.isoformat(),
        }

    @staticmethod
    def _parse_pgm_fusion(payload: Any) -> PgmFusionProvenance | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ValueError("PGM 融合来源元数据必须是对象")
        sources = []
        for item in payload.get("sources", []):
            transform_payload = item.get("transform", {})
            transform = PgmTransform2D(
                float(transform_payload.get("x_m", 0.0)),
                float(transform_payload.get("y_m", 0.0)),
                float(transform_payload.get("yaw_deg", 0.0)),
            )
            if not all(math.isfinite(value) for value in (transform.x_m, transform.y_m, transform.yaw_deg)):
                raise ValueError("PGM 融合二维外参必须为有限数")
            artifact_sha = item.get("artifact_sha256")
            sources.append(PgmFusionSource(
                source_id=str(item["source_id"]),
                source_map_id=str(item["source_map_id"]),
                transform=transform,
                source_frame_id=str(item.get("source_frame_id") or ""),
                device_id=str(item["device_id"]) if item.get("device_id") else None,
                device_name=str(item.get("device_name", "")),
                artifact_sha256=str(artifact_sha) if artifact_sha else None,
                existing_target_layer=bool(item.get("existing_target_layer", False)),
            ))
        target_hash = str(payload["target_pcd_sha256"])
        if len(target_hash) != 64:
            raise ValueError("PGM 融合目标 PCD 指纹无效")
        return PgmFusionProvenance(
            job_id=str(payload["job_id"]),
            target_pcd_sha256=target_hash,
            sources=tuple(sources),
            output_resolution=float(payload["output_resolution"]),
            merge_policy=str(payload.get("merge_policy", "occupied>free>unknown")),
            clipped_cells=int(payload.get("clipped_cells", 0)),
            clipped_area_m2=float(payload.get("clipped_area_m2", 0.0)),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
        )

    @staticmethod
    def _parse_provenance(payload: Any) -> MapBuildProvenance | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ValueError("build_provenance 必须为对象")
        transforms = tuple(
            MapTransform(
                source_id=str(item["source_id"]), is_primary=bool(item.get("is_primary", False)),
                translation_m=tuple(float(value) for value in item["translation_m"]),
                rotation_rpy_deg=tuple(float(value) for value in item["rotation_rpy_deg"]),
            )
            for item in payload.get("transforms", [])
        )
        if any(len(item.translation_m) != 3 or len(item.rotation_rpy_deg) != 3 for item in transforms):
            raise ValueError("融合外参必须为三维 XYZ/RPY")
        if any(
            not all(math.isfinite(value) for value in (*item.translation_m, *item.rotation_rpy_deg))
            for item in transforms
        ):
            raise ValueError("融合外参必须为有限数值")
        source_ids = tuple(str(value) for value in payload.get("source_ids", []))
        if len({value.casefold() for value in source_ids}) != len(source_ids):
            raise ValueError("构建来源不能重复")
        mode = MapBuildMode(str(payload["mode"]))
        if mode in {MapBuildMode.SINGLE, MapBuildMode.MULTI, MapBuildMode.FUSION}:
            if not source_ids or len(transforms) != len(source_ids):
                raise ValueError("构建来源与外参数量不一致")
            primary = [item for item in transforms if item.is_primary]
            if len(primary) != 1 or primary[0].source_id != payload.get("primary_source_id"):
                raise ValueError("构建来源必须指定唯一主坐标系")
            if primary[0].translation_m != (0.0, 0.0, 0.0) or primary[0].rotation_rpy_deg != (0.0, 0.0, 0.0):
                raise ValueError("主坐标系必须使用单位变换")
            if set(item.source_id for item in transforms) != set(source_ids):
                raise ValueError("外参来源与构建来源不一致")
        provenance = MapBuildProvenance(
            mode=mode, job_id=str(payload["job_id"]),
            primary_source_id=str(payload["primary_source_id"]) if payload.get("primary_source_id") else None,
            source_ids=source_ids,
            transforms=transforms,
            algorithm_id=str(payload["algorithm_id"]) if payload.get("algorithm_id") else None,
            algorithm_version=str(payload["algorithm_version"]) if payload.get("algorithm_version") else None,
            algorithm_sha256=str(payload["algorithm_sha256"]) if payload.get("algorithm_sha256") else None,
            excluded_device_ids=tuple(str(value) for value in payload.get("excluded_device_ids", [])),
            started_at=datetime.fromisoformat(payload["started_at"]) if payload.get("started_at") else None,
            ended_at=datetime.fromisoformat(payload["ended_at"]) if payload.get("ended_at") else None,
        )
        if not provenance.job_id:
            raise ValueError("构建任务 ID 不能为空")
        return provenance

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
