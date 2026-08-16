from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .models import PgmMapMetadata


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
