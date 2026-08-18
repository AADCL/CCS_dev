"""Open3D point-to-point ICP map-fusion plugin for CCS.

Install the ground-station requirements before importing this plugin. User
extrinsics provide the coarse alignment; ICP estimates only the residual.
"""

from pathlib import Path

import numpy as np
import open3d as o3d

from ccs_monitor.map_building import write_binary_pcd
from ccs_monitor.map_fusion import transform_points
from ccs_monitor.point_cloud import MapPointCloudLoader


PLUGIN_API_VERSION = 1
ALGORITHM_ID = "open3d_point_to_point_icp"
DISPLAY_NAME = "Open3D ICP 配准融合"
VERSION = "0.1.0"
DEFAULT_OPTIONS = {
    "voxel_size_m": 0.10,
    "max_correspondence_distance_m": 0.30,
    "max_iterations": 80,
    "min_fitness": 0.20,
}


def _option(options, name, cast, minimum, maximum):
    try:
        value = cast(options.get(name, DEFAULT_OPTIONS[name]))
    except (TypeError, ValueError) as exc:
        raise ValueError("%s 参数无效" % name) from exc
    if not np.isfinite(value) or value < minimum or value > maximum:
        raise ValueError("%s 超出允许范围" % name)
    return value


def _cloud(points):
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=np.float64))
    return cloud


def fuse_maps(pcd_files, primary_frame, transforms_to_primary, output_pcd, options):
    """Apply coarse extrinsics, refine secondary clouds with ICP, and merge."""
    del primary_frame
    if len(pcd_files) != len(transforms_to_primary) or not pcd_files:
        raise ValueError("PCD 与外参数量不一致")
    voxel_size = _option(options, "voxel_size_m", float, 1e-5, 1000.0)
    max_distance = _option(
        options, "max_correspondence_distance_m", float, 1e-5, 1000.0
    )
    iterations = _option(options, "max_iterations", int, 1, 100000)
    minimum_fitness = _option(options, "min_fitness", float, 0.0, 1.0)
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
    merged = _cloud(transformed[primary_index])
    fitness_values = []
    for index, points in enumerate(transformed):
        if index == primary_index:
            continue
        source = _cloud(points)
        source_sample = source.voxel_down_sample(voxel_size)
        target_sample = merged.voxel_down_sample(voxel_size)
        if len(source_sample.points) < 3 or len(target_sample.points) < 3:
            raise ValueError("Open3D ICP 配准至少需要三个有效点")
        result = o3d.pipelines.registration.registration_icp(
            source_sample,
            target_sample,
            max_distance,
            np.eye(4),
            o3d.pipelines.registration.TransformationEstimationPointToPoint(),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=iterations),
        )
        if not np.isfinite(result.transformation).all() or result.fitness < minimum_fitness:
            raise ValueError(
                "第 %d 张从地图 ICP fitness 过低：%.3f < %.3f"
                % (index + 1, result.fitness, minimum_fitness)
            )
        source.transform(result.transformation)
        merged += source
        merged = merged.voxel_down_sample(voxel_size)
        fitness_values.append(float(result.fitness))
    points = np.asarray(merged.points, dtype=np.float32)
    write_binary_pcd(Path(output_pcd), points)
    detail = "单地图输出" if not fitness_values else "ICP fitness " + ", ".join(
        "%.3f" % value for value in fitness_values
    )
    return {"point_count": len(points), "message": detail}
