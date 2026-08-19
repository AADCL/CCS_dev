"""Open3D online-in-call incremental multi-scale ICP fusion plugin for CCS.

Every input PCD has one ``transforms_to_primary`` entry.  The transform
direction is always ``primary frame <- source frame``.  The plugin processes
secondary maps incrementally inside one call and writes one final XYZ PCD.
"""

import os
import uuid
from pathlib import Path

import numpy as np

from ccs_monitor.map_building import write_binary_pcd
from ccs_monitor.map_fusion import transform_points
from ccs_monitor.point_cloud import MapPointCloudLoader


PLUGIN_API_VERSION = 1
ALGORITHM_ID = "open3d_online_incremental_icp"
DISPLAY_NAME = "Open3D 在线增量多尺度 ICP"
VERSION = "0.1.0"
DEFAULT_OPTIONS = {
    "voxel_sizes_m": [0.4, 0.2, 0.1],
    "max_correspondence_distances_m": [0.6, 0.3, 0.15],
    "max_iterations": [60, 40, 30],
    "relative_fitness": 1e-6,
    "relative_rmse": 1e-6,
    "normal_radius_multiplier": 2.5,
    "normal_max_nn": 40,
    "min_registration_points": 30,
    "min_fitness": 0.35,
    "max_inlier_rmse_m": 0.15,
    "max_residual_translation_m": 0.5,
    "max_residual_rotation_deg": 10.0,
    "output_voxel_size_m": 0.1,
    "max_output_points": 5000000,
}


def _number(value, name, minimum, maximum=None, include_minimum=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s 必须为数字" % name)
    value = float(value)
    if not np.isfinite(value):
        raise ValueError("%s 必须为有限数" % name)
    below = value < minimum if include_minimum else value <= minimum
    if below or (maximum is not None and value > maximum):
        raise ValueError("%s 超出允许范围" % name)
    return value


def _integer(value, name, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("%s 必须为不小于 %d 的整数" % (name, minimum))
    return int(value)


def _array(value, name, cast):
    if not isinstance(value, (list, tuple)):
        raise ValueError("%s 必须为数组" % name)
    return [cast(item, "%s[%d]" % (name, index)) for index, item in enumerate(value)]


def _strictly_descending(values, name):
    if any(first <= second for first, second in zip(values, values[1:])):
        raise ValueError("%s 必须按粗到细严格递减" % name)


def _validated_options(options):
    if not isinstance(options, dict):
        raise ValueError("options 必须为 JSON 对象")
    unknown = sorted(set(options) - set(DEFAULT_OPTIONS))
    if unknown:
        raise ValueError("未知参数：%s" % ", ".join(unknown))
    merged = {
        name: list(value) if isinstance(value, list) else value
        for name, value in DEFAULT_OPTIONS.items()
    }
    merged.update(options)

    positive = lambda value, name: _number(value, name, 0.0)
    voxels = _array(merged["voxel_sizes_m"], "voxel_sizes_m", positive)
    distances = _array(
        merged["max_correspondence_distances_m"],
        "max_correspondence_distances_m",
        positive,
    )
    iterations = _array(merged["max_iterations"], "max_iterations", _integer)
    if not 1 <= len(voxels) <= 5:
        raise ValueError("多尺度数组长度必须在 1 到 5 之间")
    if len(voxels) != len(distances) or len(voxels) != len(iterations):
        raise ValueError("多尺度参数数组长度必须一致")
    _strictly_descending(voxels, "voxel_sizes_m")
    _strictly_descending(distances, "max_correspondence_distances_m")

    merged["voxel_sizes_m"] = voxels
    merged["max_correspondence_distances_m"] = distances
    merged["max_iterations"] = iterations
    merged["relative_fitness"] = _number(
        merged["relative_fitness"], "relative_fitness", 0.0, 1.0
    )
    merged["relative_rmse"] = _number(
        merged["relative_rmse"], "relative_rmse", 0.0, 1.0
    )
    merged["normal_radius_multiplier"] = _number(
        merged["normal_radius_multiplier"], "normal_radius_multiplier", 1.0
    )
    merged["normal_max_nn"] = _integer(merged["normal_max_nn"], "normal_max_nn")
    merged["min_registration_points"] = _integer(
        merged["min_registration_points"], "min_registration_points"
    )
    merged["min_fitness"] = _number(
        merged["min_fitness"], "min_fitness", 0.0, 1.0, include_minimum=True
    )
    for name in (
            "max_inlier_rmse_m", "max_residual_translation_m",
            "max_residual_rotation_deg", "output_voxel_size_m"):
        merged[name] = positive(merged[name], name)
    merged["max_output_points"] = _integer(
        merged["max_output_points"], "max_output_points"
    )
    return merged


def _validate_primary(transforms_to_primary):
    primary_indices = [
        index for index, transform in enumerate(transforms_to_primary)
        if bool(transform.get("is_primary", False))
    ]
    if len(primary_indices) != 1:
        raise ValueError("必须且只能有一个使用单位变换的主坐标系")
    primary_index = primary_indices[0]
    transform = transforms_to_primary[primary_index]
    try:
        translation = tuple(float(value) for value in transform["translation_m"])
        rotation = tuple(float(value) for value in transform["rotation_rpy_deg"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("必须且只能有一个使用单位变换的主坐标系") from exc
    if translation != (0.0, 0.0, 0.0) or rotation != (0.0, 0.0, 0.0):
        raise ValueError("必须且只能有一个使用单位变换的主坐标系")
    return primary_index


def _load_transformed_clouds(pcd_files, transforms_to_primary):
    loader = MapPointCloudLoader()
    return [
        np.asarray(transform_points(loader.load(path).points, transform), dtype=np.float64)
        for path, transform in zip(pcd_files, transforms_to_primary)
    ]


def _voxel_downsample_numpy(points, voxel_size):
    points = np.asarray(points, dtype=np.float64)
    if not len(points):
        return np.empty((0, 3), dtype=np.float64)
    keys = np.floor(points / voxel_size).astype(np.int64)
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    sums = np.zeros((int(inverse.max()) + 1, 3), dtype=np.float64)
    counts = np.zeros(len(sums), dtype=np.int64)
    np.add.at(sums, inverse, points)
    np.add.at(counts, inverse, 1)
    return sums / counts[:, None]


def _write_atomic(output_pcd, points, maximum):
    output = Path(output_pcd)
    array = np.asarray(points, dtype=np.float32)
    if len(array) > maximum:
        raise ValueError(
            "输出点数 %d 超过 max_output_points=%d" % (len(array), maximum)
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(
        "%s.%s.tmp.pcd" % (output.name, uuid.uuid4().hex)
    )
    try:
        write_binary_pcd(temporary, array)
        validated = MapPointCloudLoader().load(temporary)
        if validated.point_count != len(array):
            raise ValueError("临时 PCD 点数校验失败")
        os.replace(str(temporary), str(output))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return len(array)


def _import_open3d():
    try:
        import open3d as o3d
    except ImportError as exc:
        raise RuntimeError(
            "多地图增量 ICP 需要 open3d>=0.18,<1，请在地面站 Python 环境安装"
        ) from exc
    version_text = str(getattr(o3d, "__version__", "0.0")).split("+")[0]
    try:
        version = tuple(int(part) for part in version_text.split(".")[:2])
    except ValueError as exc:
        raise RuntimeError("无法识别 Open3D 版本：%s" % version_text) from exc
    if version < (0, 18) or version >= (1, 0):
        raise RuntimeError(
            "Open3D 版本必须满足 open3d>=0.18,<1，当前为 %s" % version_text
        )
    return o3d


def _rotation_angle_deg(transform_matrix):
    matrix = np.asarray(transform_matrix, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("ICP 残差变换必须是有限 4x4 矩阵")
    cosine = (float(np.trace(matrix[:3, :3])) - 1.0) * 0.5
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def _open3d_cloud(o3d, points):
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=np.float64))
    return cloud


def _sample_with_normals(o3d, points, voxel_size, options, source_index, role):
    sample = _open3d_cloud(o3d, points).voxel_down_sample(voxel_size)
    point_count = len(sample.points)
    minimum = options["min_registration_points"]
    if point_count < minimum:
        raise ValueError(
            "输入索引 %d 的%s点数不足：%d < min_registration_points=%d"
            % (source_index, role, point_count, minimum)
        )
    sample.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(
            radius=voxel_size * options["normal_radius_multiplier"],
            max_nn=options["normal_max_nn"],
        )
    )
    return sample


def _registration_metrics(result, options, source_index, source_id):
    matrix = np.asarray(result.transformation, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("输入索引 %d 的 ICP 返回无效残差变换" % source_index)
    fitness = float(result.fitness)
    rmse = float(result.inlier_rmse)
    if not np.isfinite(fitness) or fitness < options["min_fitness"]:
        raise ValueError(
            "输入索引 %d 的 ICP fitness 过低：%.6f < %.6f"
            % (source_index, fitness, options["min_fitness"])
        )
    if not np.isfinite(rmse) or rmse > options["max_inlier_rmse_m"]:
        raise ValueError(
            "输入索引 %d 的 ICP inlier RMSE 过高：%.6f m > %.6f m"
            % (source_index, rmse, options["max_inlier_rmse_m"])
        )
    translation = float(np.linalg.norm(matrix[:3, 3]))
    rotation = _rotation_angle_deg(matrix)
    if translation > options["max_residual_translation_m"]:
        raise ValueError(
            "输入索引 %d 的 ICP 残差平移过大：%.6f m > %.6f m"
            % (source_index, translation, options["max_residual_translation_m"])
        )
    if rotation > options["max_residual_rotation_deg"]:
        raise ValueError(
            "输入索引 %d 的 ICP 残差旋转过大：%.6f deg > %.6f deg"
            % (source_index, rotation, options["max_residual_rotation_deg"])
        )
    return {
        "source_index": int(source_index),
        "source_id": str(source_id),
        "fitness": fitness,
        "inlier_rmse_m": rmse,
        "residual_translation_m": translation,
        "residual_rotation_deg": rotation,
    }


def _multi_scale_register(o3d, source, target, options, source_index, source_id):
    residual = np.eye(4, dtype=np.float64)
    result = None
    scales = zip(
        options["voxel_sizes_m"],
        options["max_correspondence_distances_m"],
        options["max_iterations"],
    )
    for voxel_size, maximum_distance, iterations in scales:
        source_sample = _sample_with_normals(
            o3d, source, voxel_size, options, source_index, "源点云"
        )
        target_sample = _sample_with_normals(
            o3d, target, voxel_size, options, source_index, "累计目标点云"
        )
        result = o3d.pipelines.registration.registration_icp(
            source_sample,
            target_sample,
            maximum_distance,
            residual,
            o3d.pipelines.registration.TransformationEstimationPointToPlane(),
            o3d.pipelines.registration.ICPConvergenceCriteria(
                relative_fitness=options["relative_fitness"],
                relative_rmse=options["relative_rmse"],
                max_iteration=iterations,
            ),
        )
        residual = np.asarray(result.transformation, dtype=np.float64)
    if result is None:
        raise RuntimeError("多尺度 ICP 没有执行任何尺度")
    metrics = _registration_metrics(result, options, source_index, source_id)
    aligned = np.asarray(source, dtype=np.float64) @ residual[:3, :3].T + residual[:3, 3]
    if not np.isfinite(aligned).all():
        raise ValueError("输入索引 %d 的 ICP 输出包含非有限坐标" % source_index)
    return aligned, metrics


def fuse_maps(pcd_files, primary_frame, transforms_to_primary, output_pcd, options):
    """Incrementally register complete PCD maps and write one binary XYZ PCD."""
    configured = _validated_options(options)
    if len(pcd_files) != len(transforms_to_primary) or not pcd_files:
        raise ValueError("PCD 与外参数量不一致")
    primary_index = _validate_primary(transforms_to_primary)
    transformed = _load_transformed_clouds(pcd_files, transforms_to_primary)
    accumulated = _voxel_downsample_numpy(
        transformed[primary_index], configured["output_voxel_size_m"]
    )
    if len(accumulated) > configured["max_output_points"]:
        raise ValueError(
            "主地图点数 %d 超过 max_output_points=%d"
            % (len(accumulated), configured["max_output_points"])
        )
    registrations = []
    if len(transformed) > 1:
        o3d = _import_open3d()
        for index, source in enumerate(transformed):
            if index == primary_index:
                continue
            source_id = transforms_to_primary[index].get("source_id", index)
            aligned, metrics = _multi_scale_register(
                o3d, source, accumulated, configured, index, source_id
            )
            accumulated = _voxel_downsample_numpy(
                np.concatenate((accumulated, aligned), axis=0),
                configured["output_voxel_size_m"],
            )
            if len(accumulated) > configured["max_output_points"]:
                raise ValueError(
                    "融合输入索引 %d 后点数 %d 超过 max_output_points=%d"
                    % (index, len(accumulated), configured["max_output_points"])
                )
            registrations.append(metrics)
    point_count = _write_atomic(
        output_pcd, accumulated, configured["max_output_points"]
    )
    return {
        "point_count": point_count,
        "source_count": len(transformed),
        "registered_source_count": len(registrations),
        "primary_frame": str(primary_frame),
        "registrations": registrations,
        "message": (
            "单地图输出" if not registrations
            else "在线增量多尺度 ICP 完成，共配准 %d 张从地图" % len(registrations)
        ),
    }
