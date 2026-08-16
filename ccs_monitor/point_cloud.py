from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .models import MapBounds


class PointCloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class PointCloudData:
    points: np.ndarray
    point_count: int
    bounds: MapBounds


class MapPointCloudLoader:
    """Load PCD XYZ data while keeping pypcd4 optional at application startup."""

    def __init__(self, max_render_points: int = 500_000) -> None:
        self.max_render_points = max(1, int(max_render_points))

    def load(self, path: str | Path, *, sample_for_render: bool = False) -> PointCloudData:
        source = Path(path)
        if not source.is_file():
            raise PointCloudError(f"点云文件不存在：{source}")
        points = self._load_with_pypcd4(source)
        if points is None:
            points = self._load_ascii(source)
        points = np.asarray(points, dtype=np.float32)
        if points.ndim != 2 or points.shape[1] != 3:
            raise PointCloudError("PCD 必须包含 x、y、z 三维坐标")
        if len(points) == 0:
            raise PointCloudError("PCD 不包含点数据")
        if not np.isfinite(points).all():
            raise PointCloudError("PCD 包含 NaN 或无穷坐标")

        minimum = points.min(axis=0)
        maximum = points.max(axis=0)
        bounds = MapBounds(
            float(minimum[0]), float(minimum[1]), float(minimum[2]),
            float(maximum[0]), float(maximum[1]), float(maximum[2]),
        )
        original_count = len(points)
        if sample_for_render and original_count > self.max_render_points:
            indices = np.linspace(0, original_count - 1, self.max_render_points, dtype=np.int64)
            points = points[indices]
        return PointCloudData(points=points, point_count=original_count, bounds=bounds)

    @staticmethod
    def _load_with_pypcd4(path: Path) -> np.ndarray | None:
        try:
            from pypcd4 import PointCloud
        except ImportError:
            return None
        try:
            cloud = PointCloud.from_path(str(path))
            try:
                data: Any = cloud.numpy(("x", "y", "z"))
            except TypeError:
                data = cloud.numpy(["x", "y", "z"])
            array = np.asarray(data)
            if array.dtype.names:
                return np.column_stack([array[name] for name in ("x", "y", "z")])
            if array.ndim == 2 and array.shape[1] >= 3:
                return array[:, :3]
            raise PointCloudError("pypcd4 未返回有效 XYZ 数据")
        except PointCloudError:
            raise
        except Exception as exc:
            raise PointCloudError(f"PCD 解析失败：{exc}") from exc

    @staticmethod
    def _load_ascii(path: Path) -> np.ndarray:
        try:
            with path.open("rb") as handle:
                header_lines: list[str] = []
                while True:
                    raw = handle.readline()
                    if not raw:
                        raise PointCloudError("PCD 头部缺少 DATA 声明")
                    line = raw.decode("ascii", errors="strict").strip()
                    header_lines.append(line)
                    if line.upper().startswith("DATA "):
                        data_kind = line.split(maxsplit=1)[1].lower()
                        break
                if data_kind != "ascii":
                    raise PointCloudError("binary PCD 需要安装 pypcd4")
                fields_line = next(
                    (line for line in header_lines if line.upper().startswith("FIELDS ")),
                    None,
                )
                if fields_line is None:
                    raise PointCloudError("PCD 头部缺少 FIELDS")
                fields = fields_line.split()[1:]
                count_line = next(
                    (line for line in header_lines if line.upper().startswith("COUNT ")),
                    None,
                )
                try:
                    counts = (
                        [1] * len(fields)
                        if count_line is None
                        else [int(value) for value in count_line.split()[1:]]
                    )
                except ValueError as exc:
                    raise PointCloudError("PCD COUNT 包含非法值") from exc
                if len(counts) != len(fields) or any(value < 1 for value in counts):
                    raise PointCloudError("PCD COUNT 与 FIELDS 不匹配")
                offsets: list[int] = []
                offset = 0
                for count in counts:
                    offsets.append(offset)
                    offset += count
                try:
                    xyz_indices = [offsets[fields.index(name)] for name in ("x", "y", "z")]
                except ValueError as exc:
                    raise PointCloudError("PCD FIELDS 必须包含 x、y、z") from exc
                rows: list[tuple[float, float, float]] = []
                for line_number, raw in enumerate(handle, len(header_lines) + 1):
                    text = raw.decode("ascii", errors="strict").strip()
                    if not text:
                        continue
                    values = text.split()
                    try:
                        rows.append(tuple(float(values[index]) for index in xyz_indices))
                    except (IndexError, ValueError) as exc:
                        raise PointCloudError(f"PCD 第 {line_number} 行数据无效") from exc
                return np.asarray(rows, dtype=np.float32)
        except PointCloudError:
            raise
        except (OSError, UnicodeError) as exc:
            raise PointCloudError(f"PCD 读取失败：{exc}") from exc
