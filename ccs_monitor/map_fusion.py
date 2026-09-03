from __future__ import annotations

import hashlib
import ast
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, replace
from .runtime_paths import application_root, fusion_worker_command
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .map_building import write_binary_pcd
from .models import MapFusionAlgorithm, MapTransform
from .point_cloud import MapPointCloudLoader, PointCloudError
from .static_paths import StaticPathError, StaticPathResolver


PLUGIN_API_VERSION = 1
BUILTIN_ALGORITHM_ID = "builtin_voxel_merge"
DEFAULT_REGISTRY_PATH = application_root() / "config" / "map_fusion_algorithms.json"
DEFAULT_ASSET_ROOT = application_root() / "data" / "map_fusion_algorithms"


class MapFusionError(RuntimeError):
    pass


def transform_points(points: np.ndarray, transform: MapTransform | dict[str, Any]) -> np.ndarray:
    if isinstance(transform, MapTransform):
        translation = transform.translation_m
        rpy = transform.rotation_rpy_deg
    else:
        translation = transform["translation_m"]
        rpy = transform["rotation_rpy_deg"]
    values = [*translation, *rpy]
    if len(translation) != 3 or len(rpy) != 3 or not all(math.isfinite(float(v)) for v in values):
        raise MapFusionError("地图外参必须包含有限的 XYZ 和 RPY")
    roll, pitch, yaw = np.radians(np.asarray(rpy, dtype=np.float64))
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rotation = np.asarray([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ], dtype=np.float64)
    return np.asarray(points, dtype=np.float64) @ rotation.T + np.asarray(translation, dtype=np.float64)


def voxel_merge(
    pcd_files: list[str],
    transforms_to_primary: list[dict[str, Any]],
    output_pcd: str,
    voxel_size_m: float = 0.1,
) -> dict[str, Any]:
    if len(pcd_files) != len(transforms_to_primary) or not pcd_files:
        raise MapFusionError("PCD 与外参数量不一致")
    if not math.isfinite(voxel_size_m) or voxel_size_m <= 0:
        raise MapFusionError("体素尺寸必须大于零")
    loader = MapPointCloudLoader()
    sums: dict[tuple[int, int, int], np.ndarray] = {}
    counts: dict[tuple[int, int, int], int] = {}
    for path, transform in zip(pcd_files, transforms_to_primary):
        transformed = transform_points(loader.load(path).points, transform)
        keys = np.floor(transformed / voxel_size_m).astype(np.int64)
        for key_values, point in zip(keys, transformed):
            key = tuple(int(v) for v in key_values)
            sums[key] = sums.get(key, np.zeros(3, dtype=np.float64)) + point
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(sums)
    points = np.asarray([sums[key] / counts[key] for key in ordered], dtype=np.float32)
    write_binary_pcd(output_pcd, points)
    return {"point_count": len(points), "message": "体素融合完成"}


class MapFusionRepository:
    def __init__(
        self,
        registry_path: str | Path = DEFAULT_REGISTRY_PATH,
        asset_root: str | Path = DEFAULT_ASSET_ROOT,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.asset_root = Path(asset_root)
        self.path_resolver = StaticPathResolver(self.registry_path, self.asset_root)
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self.trash_root = self.asset_root / ".trash"
        self.trash_root.mkdir(exist_ok=True)
        self._algorithms: list[MapFusionAlgorithm] = []
        self.read_only = False
        self.error_message = ""
        self.load()

    def load(self) -> list[MapFusionAlgorithm]:
        self.read_only = False
        self.error_message = ""
        if not self.registry_path.exists():
            self._algorithms = [self._builtin()]
            self._save()
            return self.algorithms()
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != 1 or not isinstance(payload.get("algorithms"), list):
                raise ValueError("schema_version 必须为 1")
            algorithms = [self._parse(item) for item in payload["algorithms"]]
            folded_ids = [item.algorithm_id.casefold() for item in algorithms]
            if len(folded_ids) != len(set(folded_ids)):
                raise ValueError("算法 ID 不能重复")
            for item in algorithms:
                if item.builtin:
                    continue
                script = Path(item.script_path)
                if not script.is_file() or hashlib.sha256(script.read_bytes()).hexdigest() != item.sha256:
                    raise ValueError(f"算法脚本缺失或指纹不匹配：{item.algorithm_id}")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, StaticPathError) as exc:
            self.read_only = True
            self.error_message = f"融合算法配置无效，已使用只读内置算法：{exc}"
            self._algorithms = [self._builtin()]
            return self.algorithms()
        configured_builtin = next(
            (item for item in algorithms if item.algorithm_id == BUILTIN_ALGORITHM_ID), None
        )
        builtin = replace(self._builtin(), is_default=bool(configured_builtin and configured_builtin.is_default))
        algorithms = [builtin] + [item for item in algorithms if item.algorithm_id != BUILTIN_ALGORITHM_ID]
        defaults = [item for item in algorithms if item.enabled and item.is_default]
        if len(defaults) > 1:
            self.read_only = True
            self.error_message = "融合算法配置无效，默认算法只能有一个"
            self._algorithms = [self._builtin()]
            return self.algorithms()
        if not defaults:
            algorithms = [replace(item, is_default=item.algorithm_id == BUILTIN_ALGORITHM_ID) for item in algorithms]
        self._algorithms = algorithms
        stored_paths = [str(item.get("script_path", "")) for item in payload["algorithms"]]
        portable_paths = [
            item.script_path if item.builtin else str(self.path_resolver.portable_asset(item.script_path))
            for item in algorithms
        ]
        if stored_paths != portable_paths:
            try:
                self._save()
            except (OSError, MapFusionError, StaticPathError) as exc:
                self.read_only = True
                self.error_message = f"融合算法路径迁移失败，配置已切换为只读：{exc}"
        return self.algorithms()

    def algorithms(self, *, enabled_only: bool = False) -> list[MapFusionAlgorithm]:
        return [item for item in self._algorithms if item.enabled or not enabled_only]

    def algorithm(self, algorithm_id: str) -> MapFusionAlgorithm | None:
        folded = algorithm_id.casefold()
        return next((item for item in self._algorithms if item.algorithm_id.casefold() == folded), None)

    def default_algorithm(self) -> MapFusionAlgorithm:
        return next(item for item in self._algorithms if item.enabled and item.is_default)

    def import_algorithm(self, source_path: str | Path, runner: "MapFusionRunner | None" = None) -> MapFusionAlgorithm:
        self._require_writable()
        source = Path(source_path)
        if source.suffix.lower() != ".py" or not source.is_file():
            raise MapFusionError("请选择有效的 .py 融合算法文件")
        metadata = self._inspect_script(source)
        if self.algorithm(metadata["algorithm_id"]):
            raise MapFusionError(f"算法 ID 已存在：{metadata['algorithm_id']}")
        target = self.asset_root / f"{metadata['algorithm_id']}_{uuid.uuid4().hex[:8]}.py"
        try:
            shutil.copy2(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            algorithm = MapFusionAlgorithm(
                metadata["algorithm_id"], metadata["display_name"], metadata["version"],
                str(target.resolve()), digest, True, False, False, metadata["default_options"],
            )
            (runner or MapFusionRunner()).validate_algorithm(algorithm)
            self._algorithms.append(algorithm)
            self._save()
            return algorithm
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def update(self, algorithm_id: str, *, enabled: bool | None = None,
               is_default: bool | None = None, default_options: dict | None = None) -> MapFusionAlgorithm:
        self._require_writable()
        current = self.algorithm(algorithm_id)
        if current is None:
            raise MapFusionError("融合算法不存在")
        if current.builtin and enabled is False:
            raise MapFusionError("内置融合算法不能禁用")
        updated = replace(
            current,
            enabled=current.enabled if enabled is None else bool(enabled),
            is_default=current.is_default if is_default is None else bool(is_default),
            default_options=current.default_options if default_options is None else dict(default_options),
        )
        if updated.is_default and not updated.enabled:
            raise MapFusionError("默认算法必须处于启用状态")
        self._algorithms = [
            replace(item, is_default=False) if updated.is_default and item.algorithm_id != updated.algorithm_id
            else updated if item.algorithm_id == updated.algorithm_id else item
            for item in self._algorithms
        ]
        if not any(item.enabled and item.is_default for item in self._algorithms):
            self._algorithms = [
                replace(item, is_default=item.algorithm_id == BUILTIN_ALGORITHM_ID)
                for item in self._algorithms
            ]
        self._save()
        return updated

    def delete(self, algorithm_id: str, *, active_algorithm_ids: Iterable[str] = ()) -> None:
        self._require_writable()
        current = self.algorithm(algorithm_id)
        if current is None:
            raise MapFusionError("融合算法不存在")
        if current.builtin:
            raise MapFusionError("内置融合算法不能删除")
        if current.algorithm_id in set(active_algorithm_ids):
            raise MapFusionError("算法正在被建图任务使用")
        source = Path(current.script_path)
        if source.is_file():
            target = self.trash_root / source.name
            if target.exists():
                target = self.trash_root / f"{source.stem}_{uuid.uuid4().hex[:8]}{source.suffix}"
            shutil.move(str(source), str(target))
        self._algorithms = [item for item in self._algorithms if item.algorithm_id != current.algorithm_id]
        if current.is_default:
            self._algorithms = [replace(item, is_default=item.algorithm_id == BUILTIN_ALGORITHM_ID) for item in self._algorithms]
        self._save()

    @staticmethod
    def _builtin() -> MapFusionAlgorithm:
        return MapFusionAlgorithm(
            BUILTIN_ALGORITHM_ID, "内置体素融合", "1.0.0", "builtin:voxel_merge",
            hashlib.sha256(b"ccs-builtin-voxel-merge-v1").hexdigest(), True, True, True,
            {"voxel_size_m": 0.1},
        )

    @staticmethod
    def _inspect_script(path: Path) -> dict[str, Any]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            constants: dict[str, Any] = {}
            has_fuse = False
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "fuse_maps":
                    has_fuse = True
                elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                        and isinstance(node.targets[0], ast.Name):
                    try:
                        constants[node.targets[0].id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise MapFusionError(f"融合算法语法检查失败：{exc}") from exc
        algorithm_id = constants.get("ALGORITHM_ID", "")
        if constants.get("PLUGIN_API_VERSION") != PLUGIN_API_VERSION:
            raise MapFusionError("PLUGIN_API_VERSION 必须为 1")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,63}", str(algorithm_id)):
            raise MapFusionError("ALGORITHM_ID 格式无效")
        if not has_fuse:
            raise MapFusionError("算法必须提供 fuse_maps()")
        options = constants.get("DEFAULT_OPTIONS", {})
        if not isinstance(options, dict):
            raise MapFusionError("DEFAULT_OPTIONS 必须为对象")
        return {
            "algorithm_id": str(algorithm_id),
            "display_name": str(constants.get("DISPLAY_NAME", algorithm_id)).strip(),
            "version": str(constants.get("VERSION", "0.1.0")).strip(),
            "default_options": options,
        }

    def _parse(self, item: dict[str, Any]) -> MapFusionAlgorithm:
        builtin = bool(item.get("builtin", False))
        stored_path = str(item["script_path"])
        script_path = stored_path if builtin else str(self.path_resolver.resolve(stored_path))
        algorithm = MapFusionAlgorithm(
            algorithm_id=str(item["algorithm_id"]), display_name=str(item["display_name"]),
            version=str(item["version"]), script_path=script_path,
            sha256=str(item["sha256"]), enabled=bool(item.get("enabled", True)),
            is_default=bool(item.get("is_default", False)), builtin=builtin,
            default_options=dict(item.get("default_options", {})),
        )
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,63}", algorithm.algorithm_id):
            raise ValueError("algorithm_id 无效")
        return algorithm

    def _save(self) -> None:
        self._require_writable()
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        algorithms = []
        for item in self._algorithms:
            serialized = asdict(item)
            if not item.builtin:
                serialized["script_path"] = self.path_resolver.portable_asset(item.script_path)
            algorithms.append(serialized)
        payload = {"schema_version": 1, "algorithms": algorithms}
        temporary = self.registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.registry_path)

    def _require_writable(self) -> None:
        if self.read_only:
            raise MapFusionError(self.error_message or "融合算法配置处于只读状态")


class MapFusionRunner:
    def __init__(self, *, timeout_seconds: float = 300.0,
                 max_input_bytes: int = 4_000_000_000,
                 max_output_bytes: int = 2_000_000_000) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.max_input_bytes = int(max_input_bytes)
        self.max_output_bytes = int(max_output_bytes)
        self.loader = MapPointCloudLoader()

    def run(self, algorithm: MapFusionAlgorithm, pcd_files: list[str | Path], primary_frame: str,
            transforms: list[MapTransform], output_pcd: str | Path,
            options: dict[str, Any] | None = None) -> dict[str, Any]:
        if not algorithm.enabled:
            raise MapFusionError("融合算法已禁用")
        if not algorithm.builtin:
            script = Path(algorithm.script_path)
            try:
                digest = hashlib.sha256(script.read_bytes()).hexdigest()
            except OSError as exc:
                raise MapFusionError(f"融合算法脚本无法读取：{exc}") from exc
            if digest != algorithm.sha256:
                raise MapFusionError("融合算法脚本指纹不匹配")
        if len(pcd_files) != len(transforms) or not pcd_files:
            raise MapFusionError("PCD 与外参数量不一致")
        try:
            input_size = sum(Path(path).stat().st_size for path in pcd_files)
        except OSError as exc:
            raise MapFusionError(f"无法读取融合输入：{exc}") from exc
        if input_size > self.max_input_bytes:
            raise MapFusionError("融合输入总大小超过配置限制")
        primary = [item for item in transforms if item.is_primary]
        if len(primary) != 1 or primary[0].translation_m != (0.0, 0.0, 0.0) or primary[0].rotation_rpy_deg != (0.0, 0.0, 0.0):
            raise MapFusionError("必须且只能有一个使用单位变换的主坐标系")
        output = Path(output_pcd)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        request = {
            "script_path": algorithm.script_path,
            "pcd_files": [str(Path(path).resolve()) for path in pcd_files],
            "primary_frame": primary_frame,
            "transforms": [asdict(item) for item in transforms],
            "output_pcd": str(output.resolve()),
            "options": dict(algorithm.default_options if options is None else options),
        }
        with tempfile.TemporaryDirectory(prefix="ccs-map-fusion-") as directory:
            request_path = Path(directory) / "request.json"
            result_path = Path(directory) / "result.json"
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            try:
                completed = subprocess.run(
                    fusion_worker_command(request_path, result_path),
                    cwd=str(application_root()), capture_output=True, text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=self.timeout_seconds, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise MapFusionError(f"融合算法运行超过 {self.timeout_seconds:g} 秒") from exc
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or "工作进程异常退出"
                raise MapFusionError(f"融合算法执行失败：{detail[-1000:]}")
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MapFusionError(f"融合算法未返回有效结果：{exc}") from exc
        if not output.is_file() or output.stat().st_size > self.max_output_bytes:
            output.unlink(missing_ok=True)
            raise MapFusionError("融合算法未生成有效 PCD 或输出超过限制")
        try:
            cloud = self.loader.load(output)
        except PointCloudError as exc:
            output.unlink(missing_ok=True)
            raise MapFusionError(f"融合 PCD 校验失败：{exc}") from exc
        if not isinstance(result, dict):
            raise MapFusionError("融合算法返回值必须为对象")
        result["point_count"] = cloud.point_count
        return result

    def validate_algorithm(self, algorithm: MapFusionAlgorithm) -> None:
        with tempfile.TemporaryDirectory(prefix="ccs-fusion-validation-") as directory:
            root = Path(directory)
            source = root / "input.pcd"
            output = root / "output.pcd"
            write_binary_pcd(source, np.asarray([(0, 0, 0), (1, 0, 0)], dtype=np.float32))
            self.run(algorithm, [source], "map", [MapTransform("validation", True)], output)
