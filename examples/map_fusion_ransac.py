"""Deterministic NumPy RANSAC map-fusion plugin for CCS.

The configured transforms are applied first. RANSAC then estimates a residual
rigid transform from each secondary cloud into the accumulated primary cloud.
Copy this file and import it from Map > Fusion Algorithms.
"""

from pathlib import Path

import numpy as np

from ccs_monitor.map_building import write_binary_pcd
from ccs_monitor.map_fusion import transform_points
from ccs_monitor.point_cloud import MapPointCloudLoader


PLUGIN_API_VERSION = 1
ALGORITHM_ID = "numpy_ransac_registration"
DISPLAY_NAME = "NumPy RANSAC 配准融合"
VERSION = "0.1.0"
DEFAULT_OPTIONS = {
    "voxel_size_m": 0.10,
    "max_sample_points": 4000,
    "iterations": 300,
    "inlier_distance_m": 0.25,
    "min_inlier_ratio": 0.20,
    "random_seed": 2026,
}


def _option(options, name, cast, minimum, maximum):
    try:
        value = cast(options.get(name, DEFAULT_OPTIONS[name]))
    except (TypeError, ValueError) as exc:
        raise ValueError("%s 参数无效" % name) from exc
    if not np.isfinite(value) or value < minimum or value > maximum:
        raise ValueError("%s 超出允许范围" % name)
    return value


def _voxel_downsample(points, voxel_size):
    if len(points) == 0:
        return np.empty((0, 3), dtype=np.float64)
    keys = np.floor(points / voxel_size).astype(np.int64)
    _, first = np.unique(keys, axis=0, return_index=True)
    return np.asarray(points[np.sort(first)], dtype=np.float64)


def _bounded_sample(points, maximum):
    if len(points) <= maximum:
        return points
    indices = np.linspace(0, len(points) - 1, maximum, dtype=np.int64)
    return points[indices]


def _nearest(source, target, block_size=256):
    """Return nearest target index and squared distance without a huge NxM array."""
    indices = np.empty(len(source), dtype=np.int64)
    distances = np.empty(len(source), dtype=np.float64)
    for start in range(0, len(source), block_size):
        block = source[start:start + block_size]
        squared = np.sum((block[:, None, :] - target[None, :, :]) ** 2, axis=2)
        nearest = np.argmin(squared, axis=1)
        indices[start:start + len(block)] = nearest
        distances[start:start + len(block)] = squared[np.arange(len(block)), nearest]
    return indices, distances


def _rigid_transform(source, target):
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        rotation = vt.T @ u.T
    translation = target_center - source_center @ rotation.T
    return rotation, translation


def _register(source, target, options, cloud_index):
    maximum = _option(options, "max_sample_points", int, 3, 50000)
    iterations = _option(options, "iterations", int, 1, 100000)
    threshold = _option(options, "inlier_distance_m", float, 1e-5, 1000.0)
    minimum_ratio = _option(options, "min_inlier_ratio", float, 0.0, 1.0)
    seed = _option(options, "random_seed", int, 0, 2 ** 32 - 1)
    source_sample = _bounded_sample(source, maximum)
    target_sample = _bounded_sample(target, maximum)
    if len(source_sample) < 3 or len(target_sample) < 3:
        raise ValueError("RANSAC 配准至少需要三个有效点")
    matches, _ = _nearest(source_sample, target_sample)
    matched_target = target_sample[matches]
    rng = np.random.default_rng(seed + cloud_index)
    threshold_squared = threshold * threshold
    best = None
    best_count = -1
    for _ in range(iterations):
        sample = rng.choice(len(source_sample), 3, replace=False)
        if np.linalg.matrix_rank(source_sample[sample] - source_sample[sample].mean(axis=0)) < 2:
            continue
        rotation, translation = _rigid_transform(source_sample[sample], matched_target[sample])
        candidate = source_sample @ rotation.T + translation
        distances = np.sum((candidate - matched_target) ** 2, axis=1)
        inliers = distances <= threshold_squared
        count = int(inliers.sum())
        if count > best_count:
            best = (rotation, translation, inliers)
            best_count = count
    ratio = best_count / float(len(source_sample))
    if best is None or ratio < minimum_ratio:
        raise ValueError(
            "第 %d 张从地图 RANSAC 重叠不足：%.3f < %.3f"
            % (cloud_index + 1, ratio, minimum_ratio)
        )
    rotation, translation, inliers = best
    candidate = source_sample @ rotation.T + translation
    nearest, distances = _nearest(candidate, target_sample)
    inliers = distances <= threshold_squared
    rotation, translation = _rigid_transform(
        source_sample[inliers], target_sample[nearest[inliers]]
    )
    return source @ rotation.T + translation, ratio


def fuse_maps(pcd_files, primary_frame, transforms_to_primary, output_pcd, options):
    """Apply coarse extrinsics, refine secondary clouds with RANSAC, and merge."""
    del primary_frame
    if len(pcd_files) != len(transforms_to_primary) or not pcd_files:
        raise ValueError("PCD 与外参数量不一致")
    voxel_size = _option(options, "voxel_size_m", float, 1e-5, 1000.0)
    loader = MapPointCloudLoader()
    transformed = [
        np.asarray(transform_points(loader.load(path).points, transform), dtype=np.float64)
        for path, transform in zip(pcd_files, transforms_to_primary)
    ]
    primary_indices = [
        index for index, transform in enumerate(transforms_to_primary)
        if bool(transform.get("is_primary", False))
    ]
    if len(primary_indices) != 1:
        raise ValueError("必须且只能指定一张主地图")
    primary_index = primary_indices[0]
    merged = transformed[primary_index]
    ratios = []
    for index, cloud in enumerate(transformed):
        if index == primary_index:
            continue
        registered, ratio = _register(
            _voxel_downsample(cloud, voxel_size),
            _voxel_downsample(merged, voxel_size),
            options,
            index,
        )
        # Estimate was calculated on the downsampled source. Re-estimate against
        # the full source by registering it directly when constructing output.
        coarse_sample = _voxel_downsample(cloud, voxel_size)
        if len(registered) != len(coarse_sample):
            raise RuntimeError("RANSAC 内部点数不一致")
        rotation, translation = _rigid_transform(coarse_sample, registered)
        merged = np.concatenate((merged, cloud @ rotation.T + translation), axis=0)
        ratios.append(ratio)
    output = _voxel_downsample(merged, voxel_size).astype(np.float32)
    write_binary_pcd(Path(output_pcd), output)
    detail = "单地图输出" if not ratios else "RANSAC 内点率 " + ", ".join("%.3f" % value for value in ratios)
    return {"point_count": len(output), "message": detail}
