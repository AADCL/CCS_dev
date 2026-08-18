"""EPGeneral 离线多 PCD 地图 ICP 配准与体素融合函数。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from .map_building import write_binary_pcd
from .map_fusion import MapFusionError, transform_points
from .point_cloud import MapPointCloudLoader


DEFAULT_OPTIONS = {
    "max_sample_points": 4000,
    "max_iterations": 30,
    "max_correspondence_distance_m": 0.50,
    "convergence_translation_m": 0.001,
    "convergence_rotation_deg": 0.05,
    "min_correspondences": 50,
    "min_source_fitness": 0.20,
    "min_target_fitness": 0.05,
    "max_inlier_rmse_m": 0.25,
    "output_voxel_size_m": 0.10,
    "max_input_points_per_map": 5_000_000,
    "max_output_voxels": 5_000_000,
}


def _option(options: dict[str, Any], name: str, integer: bool = False,
            minimum: float = 0.0, maximum: float | None = None) -> float | int:
    value = options.get(name, DEFAULT_OPTIONS[name])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MapFusionError(f"{name} 参数无效")
    if integer and not isinstance(value, int):
        raise MapFusionError(f"{name} 必须为整数")
    number = int(value) if integer else float(value)
    if not math.isfinite(float(number)) or number < minimum \
            or (maximum is not None and number > maximum):
        raise MapFusionError(f"{name} 超出允许范围")
    return number


def _settings(options: dict[str, Any]) -> dict[str, float | int]:
    return {
        "max_sample_points": _option(options, "max_sample_points", True, 3),
        "max_iterations": _option(options, "max_iterations", True, 1),
        "max_correspondence_distance_m": _option(
            options, "max_correspondence_distance_m", minimum=1e-9,
        ),
        "convergence_translation_m": _option(options, "convergence_translation_m"),
        "convergence_rotation_deg": _option(options, "convergence_rotation_deg"),
        "min_correspondences": _option(options, "min_correspondences", True, 3),
        "min_source_fitness": _option(options, "min_source_fitness", maximum=1.0),
        "min_target_fitness": _option(options, "min_target_fitness", maximum=1.0),
        "max_inlier_rmse_m": _option(options, "max_inlier_rmse_m", minimum=1e-9),
        "output_voxel_size_m": _option(options, "output_voxel_size_m", minimum=1e-9),
        "max_input_points_per_map": _option(
            options, "max_input_points_per_map", True, 1,
        ),
        "max_output_voxels": _option(options, "max_output_voxels", True, 1),
    }


def _sample(points: np.ndarray, limit: int) -> np.ndarray:
    if len(points) <= limit:
        return points
    return points[np.linspace(0, len(points) - 1, limit, dtype=np.int64)]


def _apply(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def _rotation_degrees(matrix: np.ndarray) -> float:
    cosine = max(-1.0, min(1.0, (float(np.trace(matrix[:3, :3])) - 1.0) / 2.0))
    return math.degrees(math.acos(cosine))


def _nearest(source: np.ndarray, target: np.ndarray,
             maximum_distance: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    source_indices: list[int] = []
    target_indices: list[int] = []
    distances: list[float] = []
    maximum_squared = maximum_distance * maximum_distance
    # ponytail: 分块 O(n²) 足以覆盖受 max_sample_points 限制的样本；实测成瓶颈再换 KD-tree。
    for start in range(0, len(source), 256):
        chunk = source[start:start + 256]
        squared = np.sum((chunk[:, None, :] - target[None, :, :]) ** 2, axis=2)
        nearest = np.argmin(squared, axis=1)
        nearest_squared = squared[np.arange(len(chunk)), nearest]
        valid = nearest_squared <= maximum_squared
        source_indices.extend((np.nonzero(valid)[0] + start).tolist())
        target_indices.extend(nearest[valid].tolist())
        distances.extend(np.sqrt(nearest_squared[valid]).tolist())
    return (
        np.asarray(source_indices, dtype=np.int64),
        np.asarray(target_indices, dtype=np.int64),
        np.asarray(distances, dtype=np.float64),
    )


def _best_fit(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    if np.linalg.matrix_rank(covariance) < 2:
        raise MapFusionError("ICP 对应点几何退化")
    left, _, right = np.linalg.svd(covariance)
    rotation = right.T @ left.T
    if np.linalg.det(rotation) < 0:
        right[-1] *= -1
        rotation = right.T @ left.T
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = target_center - rotation @ source_center
    return matrix


def _refine(source: np.ndarray, target: np.ndarray,
            config: dict[str, float | int]) -> tuple[np.ndarray, int]:
    matrix = np.eye(4, dtype=np.float64)
    for iteration in range(int(config["max_iterations"])):
        transformed = _apply(source, matrix)
        source_index, target_index, _ = _nearest(
            transformed, target, float(config["max_correspondence_distance_m"]),
        )
        if len(source_index) < int(config["min_correspondences"]):
            raise MapFusionError("ICP 对应点数量不足")
        delta = _best_fit(transformed[source_index], target[target_index])
        matrix = delta @ matrix
        if np.linalg.norm(delta[:3, 3]) <= config["convergence_translation_m"] \
                and _rotation_degrees(delta) <= config["convergence_rotation_deg"]:
            return matrix, iteration + 1
    return matrix, int(config["max_iterations"])


def _evaluate(source: np.ndarray, target: np.ndarray, matrix: np.ndarray,
              config: dict[str, float | int]) -> dict[str, float | int]:
    maximum = float(config["max_correspondence_distance_m"])
    source_index, _, source_distance = _nearest(_apply(source, matrix), target, maximum)
    target_index, _, target_distance = _nearest(
        _apply(target, np.linalg.inv(matrix)), source, maximum,
    )
    source_fitness = len(source_index) / float(len(source))
    target_fitness = len(target_index) / float(len(target))
    all_distances = np.concatenate((source_distance, target_distance))
    rmse = float(np.sqrt(np.mean(all_distances ** 2))) if len(all_distances) else math.inf
    if min(len(source_index), len(target_index)) < int(config["min_correspondences"]):
        raise MapFusionError("ICP 双向对应点数量不足")
    if source_fitness < config["min_source_fitness"] \
            or target_fitness < config["min_target_fitness"]:
        raise MapFusionError("ICP 重叠率低于质量门槛")
    if not math.isfinite(rmse) or rmse > config["max_inlier_rmse_m"]:
        raise MapFusionError("ICP RMSE 超过质量门槛")
    return {
        "source_fitness": source_fitness,
        "target_fitness": target_fitness,
        "inlier_rmse_m": rmse,
        "source_correspondences": len(source_index),
        "target_correspondences": len(target_index),
    }


def _voxel_centroids(clouds: list[np.ndarray], voxel_size: float,
                     maximum: int) -> np.ndarray:
    sums: dict[tuple[int, int, int], np.ndarray] = {}
    counts: dict[tuple[int, int, int], int] = {}
    for points in clouds:
        keys = np.floor(points / voxel_size).astype(np.int64)
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        cloud_sums = np.zeros((len(unique), 3), dtype=np.float64)
        np.add.at(cloud_sums, inverse, points)
        for key_values, total, count in zip(unique, cloud_sums, np.bincount(inverse)):
            key = tuple(int(value) for value in key_values)
            if key not in sums and len(sums) >= maximum:
                raise MapFusionError("融合体素数量超过配置上限")
            sums[key] = sums.get(key, np.zeros(3, dtype=np.float64)) + total
            counts[key] = counts.get(key, 0) + int(count)
    return np.asarray([sums[key] / counts[key] for key in sorted(sums)], dtype=np.float32)


def fuse_maps(pcd_files: list[str], primary_frame: str,
              transforms_to_primary: list[dict[str, Any]], output_pcd: str,
              options: dict[str, Any]) -> dict[str, Any]:
    """以平台外参为粗初值，ICP 精配准所有从地图后进行体素质心融合。"""
    del primary_frame
    if len(pcd_files) < 2 or len(pcd_files) != len(transforms_to_primary):
        raise MapFusionError("多地图融合需要至少两张 PCD，且外参数量必须一致")
    config = _settings(options)
    primary_indices = [
        index for index, transform in enumerate(transforms_to_primary)
        if bool(transform.get("is_primary", False))
    ]
    if len(primary_indices) != 1:
        raise MapFusionError("必须且只能指定一张主地图")

    loader = MapPointCloudLoader()
    clouds: list[np.ndarray] = []
    for path, transform in zip(pcd_files, transforms_to_primary):
        points = loader.load(path).points
        if len(points) > config["max_input_points_per_map"]:
            raise MapFusionError("输入 PCD 点数超过配置上限")
        clouds.append(np.asarray(transform_points(points, transform), dtype=np.float64))

    primary_index = primary_indices[0]
    primary_sample = _sample(clouds[primary_index], int(config["max_sample_points"]))
    fused_clouds = list(clouds)
    details: list[str] = []
    for index, source in enumerate(clouds):
        if index == primary_index:
            continue
        source_sample = _sample(source, int(config["max_sample_points"]))
        try:
            residual, iterations = _refine(source_sample, primary_sample, config)
            quality = _evaluate(source_sample, primary_sample, residual, config)
        except MapFusionError as exc:
            source_id = transforms_to_primary[index].get("source_id", str(index + 1))
            raise MapFusionError(f"从地图 {source_id} 配准失败：{exc}") from exc
        fused_clouds[index] = _apply(source, residual)
        details.append(
            f"#{index + 1} ICP {iterations} 次，fitness "
            f"{quality['source_fitness']:.3f}/{quality['target_fitness']:.3f}，"
            f"RMSE {quality['inlier_rmse_m']:.4f} m"
        )

    points = _voxel_centroids(
        fused_clouds, float(config["output_voxel_size_m"]),
        int(config["max_output_voxels"]),
    )
    write_binary_pcd(Path(output_pcd), points)
    return {"point_count": len(points), "message": "；".join(details)}
