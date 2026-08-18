from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .models import PgmMapMetadata
from .point_cloud import MapPointCloudLoader, PointCloudError


class PgmMapError(RuntimeError):
    pass


@dataclass(frozen=True)
class PgmMapData:
    pixels: np.ndarray
    metadata: PgmMapMetadata
    source_image_path: Path

    def rgba(self) -> np.ndarray:
        pixels = np.flipud(self.pixels).astype(np.float32) / 255.0
        occupancy = pixels if self.metadata.negate else 1.0 - pixels
        rgba = np.zeros((*pixels.shape, 4), dtype=np.float32)
        occupied = occupancy >= self.metadata.occupied_thresh
        free = occupancy <= self.metadata.free_thresh
        unknown = ~(occupied | free)
        rgba[free] = (0.05, 0.30, 0.34, 0.48)
        rgba[occupied] = (0.92, 0.20, 0.48, 0.92)
        rgba[unknown] = (0.12, 0.18, 0.28, 0.28)
        return rgba


@dataclass(frozen=True)
class PcdToPgmOptions:
    resolution: float = 0.05
    min_z: float | None = None
    max_z: float | None = None
    padding_m: float = 0.5
    min_points_per_cell: int = 1
    inflation_radius_m: float = 0.0
    empty_cell: str = "unknown"
    occupied_thresh: float = 0.65
    free_thresh: float = 0.196


@dataclass(frozen=True)
class PcdToPgmResult:
    metadata: PgmMapMetadata
    occupied_cells: int
    selected_points: int


class PcdToPgmGenerator:
    """Project finite PCD XYZ points into a ROS map_server occupancy image."""

    OCCUPIED = 0
    FREE = 254
    UNKNOWN = 205

    def __init__(self, loader: MapPointCloudLoader | None = None,
                 *, max_grid_cells: int = 20_000_000,
                 max_inflation_operations: int = 50_000_000) -> None:
        self.loader = loader or MapPointCloudLoader()
        self.max_grid_cells = max(1, int(max_grid_cells))
        self.max_inflation_operations = max(1, int(max_inflation_operations))

    def generate(
        self,
        pcd_path: str | Path,
        pgm_path: str | Path,
        yaml_path: str | Path,
        options: PcdToPgmOptions | None = None,
    ) -> PcdToPgmResult:
        settings = options or PcdToPgmOptions()
        self._validate_options(settings)
        try:
            cloud = self.loader.load(pcd_path)
        except PointCloudError as exc:
            raise PgmMapError(f"点云读取失败：{exc}") from exc
        points = np.asarray(cloud.points, dtype=np.float64)
        depth = cloud.bounds.max_z - cloud.bounds.min_z
        automatic_min = cloud.bounds.min_z + (0.15 if depth >= 0.15 else 0.0)
        min_z = automatic_min if settings.min_z is None else float(settings.min_z)
        max_z = cloud.bounds.max_z if settings.max_z is None else float(settings.max_z)
        if not np.isfinite((min_z, max_z)).all() or min_z > max_z:
            raise PgmMapError("投影高度必须为有限数且最低高度不能大于最高高度")
        selected = points[(points[:, 2] >= min_z) & (points[:, 2] <= max_z)]
        if len(selected) == 0:
            raise PgmMapError("指定高度范围内没有可用于生成栅格的点")

        resolution = float(settings.resolution)
        padding = float(settings.padding_m)
        origin_x = math.floor((float(selected[:, 0].min()) - padding) / resolution) * resolution
        origin_y = math.floor((float(selected[:, 1].min()) - padding) / resolution) * resolution
        maximum_x = float(selected[:, 0].max()) + padding
        maximum_y = float(selected[:, 1].max()) + padding
        width = max(1, int(math.floor((maximum_x - origin_x) / resolution)) + 1)
        height = max(1, int(math.floor((maximum_y - origin_y) / resolution)) + 1)
        if width * height > self.max_grid_cells:
            raise PgmMapError(
                f"生成栅格包含 {width * height} 个像素，超过上限 {self.max_grid_cells}"
            )

        x_indices = np.floor((selected[:, 0] - origin_x) / resolution).astype(np.int64)
        y_indices = np.floor((selected[:, 1] - origin_y) / resolution).astype(np.int64)
        x_indices = np.clip(x_indices, 0, width - 1)
        y_indices = np.clip(y_indices, 0, height - 1)
        counts = np.zeros((height, width), dtype=np.uint32)
        np.add.at(counts, (y_indices, x_indices), 1)
        occupied = counts >= int(settings.min_points_per_cell)
        occupied = self._inflate(
            occupied,
            float(settings.inflation_radius_m),
            resolution,
            self.max_inflation_operations,
        )
        fill = self.UNKNOWN if settings.empty_cell == "unknown" else self.FREE
        pixels = np.full((height, width), fill, dtype=np.uint8)
        pixels[occupied] = self.OCCUPIED
        stored_pixels = np.flipud(pixels)

        image_target = Path(pgm_path)
        yaml_target = Path(yaml_path)
        image_target.parent.mkdir(parents=True, exist_ok=True)
        yaml_target.parent.mkdir(parents=True, exist_ok=True)
        metadata = PgmMapMetadata(
            image_path=image_target.name,
            yaml_path=yaml_target.name,
            resolution=resolution,
            origin_x=origin_x,
            origin_y=origin_y,
            origin_yaw=0.0,
            image_width=width,
            image_height=height,
            negate=False,
            occupied_thresh=float(settings.occupied_thresh),
            free_thresh=float(settings.free_thresh),
        )
        try:
            image_target.write_bytes(
                f"P5\n{width} {height}\n255\n".encode("ascii") + stored_pixels.tobytes()
            )
            yaml_target.write_text(
                yaml.safe_dump(
                    PgmMapLoader.normalized_yaml(metadata),
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        except (OSError, yaml.YAMLError) as exc:
            image_target.unlink(missing_ok=True)
            yaml_target.unlink(missing_ok=True)
            raise PgmMapError(f"PGM 生成文件写入失败：{exc}") from exc
        return PcdToPgmResult(metadata, int(occupied.sum()), len(selected))

    @staticmethod
    def _inflate(occupied: np.ndarray, radius_m: float, resolution: float,
                 max_operations: int) -> np.ndarray:
        radius = int(math.ceil(radius_m / resolution))
        if radius <= 0 or not occupied.any():
            return occupied
        result = occupied.copy()
        rows, columns = np.nonzero(occupied)
        offsets = [
            (dy, dx)
            for dy in range(-radius, radius + 1)
            for dx in range(-radius, radius + 1)
            if dx * dx + dy * dy <= radius * radius
        ]
        if len(offsets) * len(rows) > max_operations:
            raise PgmMapError("障碍膨胀计算量超过安全上限，请增大分辨率或减小膨胀半径")
        for dy, dx in offsets:
            target_rows = rows + dy
            target_columns = columns + dx
            valid = (
                (target_rows >= 0) & (target_rows < occupied.shape[0])
                & (target_columns >= 0) & (target_columns < occupied.shape[1])
            )
            result[target_rows[valid], target_columns[valid]] = True
        return result

    @staticmethod
    def _validate_options(options: PcdToPgmOptions) -> None:
        values = (
            options.resolution, options.padding_m, options.inflation_radius_m,
            options.occupied_thresh, options.free_thresh,
        )
        if not np.isfinite(values).all():
            raise PgmMapError("PGM 生成参数必须为有限数")
        if options.resolution <= 0 or options.resolution > 1000:
            raise PgmMapError("分辨率必须大于 0 且不超过 1000 米")
        if options.padding_m < 0 or options.inflation_radius_m < 0:
            raise PgmMapError("边缘留白和障碍膨胀半径不能为负数")
        if (
            not isinstance(options.min_points_per_cell, int)
            or isinstance(options.min_points_per_cell, bool)
            or options.min_points_per_cell < 1
        ):
            raise PgmMapError("单栅格最少点数必须是正整数")
        if options.empty_cell not in {"unknown", "free"}:
            raise PgmMapError("未命中栅格类型必须为 unknown 或 free")
        if not 0 <= options.free_thresh < options.occupied_thresh <= 1:
            raise PgmMapError("阈值必须满足 0 <= free_thresh < occupied_thresh <= 1")


class PgmMapLoader:
    REQUIRED_KEYS = (
        "image", "resolution", "origin", "negate", "occupied_thresh", "free_thresh"
    )

    def load_yaml(self, yaml_path: str | Path) -> PgmMapData:
        source_yaml = Path(yaml_path)
        if source_yaml.suffix.lower() not in {".yaml", ".yml"}:
            raise PgmMapError("请选择 ROS map_server YAML 文件")
        try:
            payload = yaml.safe_load(source_yaml.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise PgmMapError(f"地图 YAML 读取失败：{exc}") from exc
        if not isinstance(payload, dict):
            raise PgmMapError("地图 YAML 根节点必须是对象")
        missing = [key for key in self.REQUIRED_KEYS if key not in payload]
        if missing:
            raise PgmMapError(f"地图 YAML 缺少字段：{', '.join(missing)}")
        try:
            image_value = payload["image"]
            if not isinstance(image_value, str) or not image_value.strip():
                raise ValueError("image 必须是非空字符串")
            image_path = Path(image_value)
            if not image_path.is_absolute():
                image_path = source_yaml.parent / image_path
            resolution = float(payload["resolution"])
            origin = payload["origin"]
            if not isinstance(origin, list) or len(origin) != 3:
                raise ValueError("origin 必须是 [x, y, yaw]")
            origin_values = tuple(float(value) for value in origin)
            negate_raw = payload["negate"]
            if isinstance(negate_raw, bool):
                negate = negate_raw
            elif isinstance(negate_raw, int) and negate_raw in {0, 1}:
                negate = bool(negate_raw)
            else:
                raise ValueError("negate 必须是 0、1 或布尔值")
            occupied = float(payload["occupied_thresh"])
            free = float(payload["free_thresh"])
        except (TypeError, ValueError) as exc:
            raise PgmMapError(f"地图 YAML 字段无效：{exc}") from exc
        if not np.isfinite((resolution, *origin_values, occupied, free)).all():
            raise PgmMapError("地图 YAML 数值必须为有限数")
        if resolution <= 0:
            raise PgmMapError("resolution 必须大于 0")
        if not 0 <= free < occupied <= 1:
            raise PgmMapError("阈值必须满足 0 <= free_thresh < occupied_thresh <= 1")
        pixels = self.load_pgm(image_path)
        metadata = PgmMapMetadata(
            image_path="map.pgm",
            yaml_path="map.yaml",
            resolution=resolution,
            origin_x=origin_values[0],
            origin_y=origin_values[1],
            origin_yaw=origin_values[2],
            image_width=int(pixels.shape[1]),
            image_height=int(pixels.shape[0]),
            negate=negate,
            occupied_thresh=occupied,
            free_thresh=free,
        )
        return PgmMapData(pixels, metadata, image_path.resolve())

    def load_pgm(self, path: str | Path) -> np.ndarray:
        source = Path(path)
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise PgmMapError(f"PGM 读取失败：{exc}") from exc
        index = 0

        def token() -> bytes:
            nonlocal index
            while index < len(raw):
                if raw[index] == 35:
                    while index < len(raw) and raw[index] not in (10, 13):
                        index += 1
                elif raw[index] in b" \t\r\n":
                    index += 1
                else:
                    break
            start = index
            while index < len(raw) and raw[index] not in b" \t\r\n#":
                index += 1
            if start == index:
                raise PgmMapError("PGM 头部不完整")
            return raw[start:index]

        try:
            magic = token()
            width = int(token())
            height = int(token())
            max_value = int(token())
        except (ValueError, PgmMapError) as exc:
            raise PgmMapError(f"PGM 头部无效：{exc}") from exc
        if magic not in {b"P2", b"P5"}:
            raise PgmMapError("仅支持 P2/P5 灰度 PGM")
        if width <= 0 or height <= 0 or not 0 < max_value <= 65535:
            raise PgmMapError("PGM 尺寸或最大灰度无效")
        count = width * height
        if magic == b"P2":
            try:
                values = np.asarray([int(token()) for _ in range(count)], dtype=np.float64)
            except (ValueError, PgmMapError) as exc:
                raise PgmMapError(f"PGM 像素数据无效：{exc}") from exc
        else:
            if index >= len(raw) or raw[index] not in b" \t\r\n":
                raise PgmMapError("PGM 头部与二进制数据之间缺少空白符")
            if raw[index:index + 2] == b"\r\n":
                index += 2
            else:
                index += 1
            bytes_per_pixel = 1 if max_value < 256 else 2
            expected = count * bytes_per_pixel
            payload = raw[index:index + expected]
            if len(payload) != expected:
                raise PgmMapError("PGM 像素数据长度不足")
            dtype = np.uint8 if bytes_per_pixel == 1 else np.dtype(">u2")
            values = np.frombuffer(payload, dtype=dtype).astype(np.float64)
        if np.any(values < 0) or np.any(values > max_value):
            raise PgmMapError("PGM 像素超出最大灰度范围")
        normalized = np.rint(values * (255.0 / max_value)).astype(np.uint8)
        return normalized.reshape((height, width))

    @staticmethod
    def normalized_yaml(metadata: PgmMapMetadata) -> dict[str, Any]:
        return {
            "image": metadata.image_path,
            "resolution": metadata.resolution,
            "origin": [metadata.origin_x, metadata.origin_y, metadata.origin_yaw],
            "negate": int(metadata.negate),
            "occupied_thresh": metadata.occupied_thresh,
            "free_thresh": metadata.free_thresh,
        }
