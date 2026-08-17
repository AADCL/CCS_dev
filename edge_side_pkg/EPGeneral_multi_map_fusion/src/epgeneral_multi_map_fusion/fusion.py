import hashlib
import io
import json
import math
import os
import tempfile
from collections import deque
from datetime import datetime

import numpy as np
import yaml

from .config import load_config
from .pcd import PcdError, load_xyz, write_binary_xyz


class FusionError(ValueError):
    pass


def _read_yaml(path):
    try:
        with io.open(path, "r", encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except (IOError, yaml.YAMLError) as exc:
        raise FusionError("cannot read job %s: %s" % (path, exc))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise FusionError("job schema_version must be 1")
    return value


def _required_text(parent, key):
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FusionError("%s must be a non-empty string" % key)
    return value.strip()


def _resolve(base, value):
    return os.path.abspath(value if os.path.isabs(value) else os.path.join(base, value))


def _pose_matrix(value):
    if not isinstance(value, dict):
        raise FusionError("T_target_from_source must be a mapping")
    numbers = {}
    for key in ("x", "y", "z", "qx", "qy", "qz", "qw"):
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise FusionError("T_target_from_source.%s must be finite" % key)
        numbers[key] = float(item)
    quaternion = np.asarray(
        [numbers["qx"], numbers["qy"], numbers["qz"], numbers["qw"]], dtype=np.float64
    )
    norm = np.linalg.norm(quaternion)
    if norm < 1e-9:
        raise FusionError("T_target_from_source quaternion must not be zero")
    x, y, z, w = quaternion / norm
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = np.asarray(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    matrix[:3, 3] = [numbers["x"], numbers["y"], numbers["z"]]
    return matrix


def _validate_rigid(matrix):
    value = np.asarray(matrix, dtype=np.float64)
    if value.shape != (4, 4) or not np.isfinite(value).all():
        raise FusionError("transform must be a finite 4x4 matrix")
    if not np.allclose(value[3], [0.0, 0.0, 0.0, 1.0], atol=1e-6):
        raise FusionError("transform last row is invalid")
    rotation = value[:3, :3]
    if not np.allclose(rotation.T.dot(rotation), np.eye(3), atol=1e-5):
        raise FusionError("transform rotation is not orthogonal")
    if abs(np.linalg.det(rotation) - 1.0) > 1e-5:
        raise FusionError("transform rotation determinant must be 1")
    return value


def _load_job(path, config):
    root = _read_yaml(path)
    base = os.path.dirname(os.path.abspath(path))
    reference = _required_text(root, "reference_map_id")
    raw_maps = root.get("maps")
    if not isinstance(raw_maps, list) or len(raw_maps) < 2:
        raise FusionError("maps must contain at least 2 entries")

    maps = []
    map_ids = set()
    input_paths = set()
    for item in raw_maps:
        if not isinstance(item, dict):
            raise FusionError("each map must be a mapping")
        map_id = _required_text(item, "map_id")
        pcd_path = _resolve(base, _required_text(item, "pcd_path"))
        if map_id in map_ids:
            raise FusionError("duplicate map_id: %s" % map_id)
        real_path = os.path.realpath(pcd_path)
        if real_path in input_paths:
            raise FusionError("the same PCD cannot be selected twice")
        map_ids.add(map_id)
        input_paths.add(real_path)
        maps.append({"map_id": map_id, "pcd_path": pcd_path})
    if reference not in map_ids:
        raise FusionError("reference_map_id must identify an input map")

    raw_edges = root.get("placements")
    if not isinstance(raw_edges, list) or len(raw_edges) < len(maps) - 1:
        raise FusionError("placements must connect all input maps")
    edges = []
    pairs = set()
    for item in raw_edges:
        if not isinstance(item, dict):
            raise FusionError("each placement must be a mapping")
        source = _required_text(item, "source_map_id")
        target = _required_text(item, "target_map_id")
        if source == target or source not in map_ids or target not in map_ids:
            raise FusionError("placement map IDs are invalid")
        pair = tuple(sorted((source, target)))
        if pair in pairs:
            raise FusionError("duplicate placement between %s and %s" % pair)
        pairs.add(pair)
        kind = item.get("kind", "calibration")
        if kind not in ("calibration", "registration"):
            raise FusionError("placement kind must be calibration or registration")
        edges.append(
            {
                "source_map_id": source,
                "target_map_id": target,
                "kind": kind,
                "matrix": _validate_rigid(_pose_matrix(item.get("T_target_from_source"))),
            }
        )

    output = root.get("output")
    if not isinstance(output, dict):
        raise FusionError("output must be a mapping")
    pcd_path = _resolve(base, _required_text(output, "pcd_path"))
    report_value = output.get("report_path")
    report_path = _resolve(base, report_value.strip()) if isinstance(report_value, str) and report_value.strip() else pcd_path + ".json"
    if os.path.realpath(pcd_path) in input_paths:
        raise FusionError("output PCD must not overwrite an input map")
    if os.path.splitext(pcd_path)[1].lower() != ".pcd":
        raise FusionError("output.pcd_path must end with .pcd")
    if pcd_path == report_path:
        raise FusionError("output PCD and report paths must differ")
    if os.path.exists(pcd_path) or os.path.exists(report_path):
        raise FusionError("output path already exists")
    return {
        "reference_map_id": reference,
        "maps": maps,
        "edges": edges,
        "output_pcd_path": pcd_path,
        "report_path": report_path,
    }


def _fingerprint(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _sample(points, limit):
    if len(points) <= limit:
        return points
    indices = np.linspace(0, len(points) - 1, limit, dtype=np.int64)
    return points[indices]


def _transform(points, matrix):
    return points.dot(matrix[:3, :3].T) + matrix[:3, 3]


def _rotation_degrees(matrix):
    cosine = max(-1.0, min(1.0, (float(np.trace(matrix[:3, :3])) - 1.0) / 2.0))
    return math.degrees(math.acos(cosine))


def _nearest(source, target, maximum_distance):
    source_indices = []
    target_indices = []
    distances = []
    maximum_squared = maximum_distance * maximum_distance
    # ponytail: chunked O(n^2) nearest-neighbour search; replace with a KD-tree only
    # when configured sample sizes or measured runtime make it the actual bottleneck.
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


def _best_fit(source, target):
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T.dot(target - target_center)
    if np.linalg.matrix_rank(covariance) < 2:
        raise FusionError("registration correspondences are geometrically degenerate")
    left, _, right = np.linalg.svd(covariance)
    rotation = right.T.dot(left.T)
    if np.linalg.det(rotation) < 0:
        right[-1, :] *= -1
        rotation = right.T.dot(left.T)
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = target_center - rotation.dot(source_center)
    return _validate_rigid(matrix)


def _refine(source, target, initial, config):
    matrix = initial.copy()
    for iteration in range(config["max_iterations"]):
        transformed = _transform(source, matrix)
        source_index, target_index, _ = _nearest(
            transformed, target, config["max_correspondence_distance_m"]
        )
        if len(source_index) < config["min_correspondences"]:
            raise FusionError("registration has too few correspondences")
        delta = _best_fit(transformed[source_index], target[target_index])
        matrix = _validate_rigid(delta.dot(matrix))
        if (
            np.linalg.norm(delta[:3, 3]) <= config["convergence_translation_m"]
            and _rotation_degrees(delta) <= config["convergence_rotation_deg"]
        ):
            return matrix, iteration + 1
    return matrix, config["max_iterations"]


def _evaluate(source, target, matrix, config):
    maximum = config["max_correspondence_distance_m"]
    transformed = _transform(source, matrix)
    source_index, _, source_distance = _nearest(transformed, target, maximum)
    inverse = np.linalg.inv(matrix)
    reverse = _transform(target, inverse)
    target_index, _, target_distance = _nearest(reverse, source, maximum)
    source_fitness = float(len(source_index)) / float(len(source))
    target_fitness = float(len(target_index)) / float(len(target))
    all_distances = np.concatenate((source_distance, target_distance))
    rmse = float(np.sqrt(np.mean(all_distances ** 2))) if len(all_distances) else float("inf")
    metrics = {
        "source_fitness": source_fitness,
        "target_fitness": target_fitness,
        "inlier_rmse_m": rmse,
        "source_correspondences": int(len(source_index)),
        "target_correspondences": int(len(target_index)),
    }
    if min(len(source_index), len(target_index)) < config["min_correspondences"]:
        raise FusionError("registration quality failed: too few bidirectional correspondences")
    if source_fitness < config["min_source_fitness"] or target_fitness < config["min_target_fitness"]:
        raise FusionError("registration quality failed: fitness below threshold")
    if not math.isfinite(rmse) or rmse > config["max_inlier_rmse_m"]:
        raise FusionError("registration quality failed: RMSE above threshold")
    return metrics


def _global_transforms(reference, map_ids, edges, config):
    adjacency = dict((map_id, []) for map_id in map_ids)
    for edge in edges:
        source = edge["source_map_id"]
        target = edge["target_map_id"]
        target_from_source = edge["matrix"]
        adjacency[target].append((source, target_from_source))
        adjacency[source].append((target, np.linalg.inv(target_from_source)))

    transforms = {reference: np.eye(4, dtype=np.float64)}
    queue = deque([reference])
    while queue:
        current = queue.popleft()
        for neighbor, current_from_neighbor in adjacency[current]:
            if neighbor in transforms:
                continue
            transforms[neighbor] = _validate_rigid(transforms[current].dot(current_from_neighbor))
            queue.append(neighbor)
    missing = sorted(set(map_ids) - set(transforms))
    if missing:
        raise FusionError("placement graph is disconnected: %s" % ", ".join(missing))

    # ponytail: calibrated graph composition is sufficient for this first package;
    # add pose-graph optimization when real loop residuals show it is necessary.
    for edge in edges:
        source = edge["source_map_id"]
        target = edge["target_map_id"]
        predicted = transforms[target].dot(edge["matrix"])
        difference = np.linalg.inv(transforms[source]).dot(predicted)
        translation_error = float(np.linalg.norm(difference[:3, 3]))
        rotation_error = _rotation_degrees(difference)
        edge["cycle_translation_error_m"] = translation_error
        edge["cycle_rotation_error_deg"] = rotation_error
        if translation_error > config["max_cycle_translation_error_m"] or rotation_error > config["max_cycle_rotation_error_deg"]:
            raise FusionError("placement cycle is inconsistent at %s -> %s" % (source, target))
    return transforms


class _VoxelAccumulator(object):
    def __init__(self, voxel_size, maximum):
        self.voxel_size = voxel_size
        self.maximum = maximum
        self.voxels = {}

    def add(self, points):
        keys = np.floor(points / self.voxel_size).astype(np.int64)
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        sums = np.zeros((len(unique), 3), dtype=np.float64)
        np.add.at(sums, inverse, points)
        counts = np.bincount(inverse)
        for key_values, total, count in zip(unique, sums, counts):
            key = tuple(int(value) for value in key_values)
            previous = self.voxels.get(key)
            if previous is None:
                if len(self.voxels) >= self.maximum:
                    raise FusionError("fused voxel count exceeds configured limit")
                self.voxels[key] = (total, int(count))
            else:
                self.voxels[key] = (previous[0] + total, previous[1] + int(count))

    def points(self):
        ordered = sorted(self.voxels)
        return np.asarray(
            [self.voxels[key][0] / self.voxels[key][1] for key in ordered], dtype=np.float64
        )


def _temporary_path(target, suffix):
    directory = os.path.dirname(target) or os.curdir
    if not os.path.isdir(directory):
        os.makedirs(directory)
    descriptor, path = tempfile.mkstemp(prefix=".multi-map-fusion-", suffix=suffix, dir=directory)
    os.close(descriptor)
    return path


def run_fusion(config_path, job_path):
    config = load_config(config_path)
    job = _load_job(job_path, config)
    samples = {}
    input_metadata = []
    try:
        for item in job["maps"]:
            points = load_xyz(item["pcd_path"], config["max_input_points_per_map"])
            samples[item["map_id"]] = _sample(points, config["max_sample_points"])
            input_metadata.append(
                {
                    "map_id": item["map_id"],
                    "pcd_path": item["pcd_path"],
                    "point_count": int(len(points)),
                    "sha256": _fingerprint(item["pcd_path"]),
                }
            )

        edge_reports = []
        for edge in job["edges"]:
            initial = edge["matrix"].copy()
            iterations = 0
            metrics = None
            if edge["kind"] == "registration":
                source = samples[edge["source_map_id"]]
                target = samples[edge["target_map_id"]]
                try:
                    if config["registration_enabled"]:
                        edge["matrix"], iterations = _refine(source, target, initial, config)
                    metrics = _evaluate(source, target, edge["matrix"], config)
                except FusionError as exc:
                    raise FusionError(
                        "registration %s -> %s failed: %s"
                        % (edge["source_map_id"], edge["target_map_id"], exc)
                    )
            edge_reports.append(
                {
                    "source_map_id": edge["source_map_id"],
                    "target_map_id": edge["target_map_id"],
                    "kind": edge["kind"],
                    "T_target_from_source_initial": initial.tolist(),
                    "T_target_from_source": edge["matrix"].tolist(),
                    "iterations": iterations,
                    "quality": metrics,
                }
            )

        transforms = _global_transforms(
            job["reference_map_id"], [item["map_id"] for item in job["maps"]], job["edges"], config
        )
        for report, edge in zip(edge_reports, job["edges"]):
            report["cycle_translation_error_m"] = edge["cycle_translation_error_m"]
            report["cycle_rotation_error_deg"] = edge["cycle_rotation_error_deg"]

        accumulator = _VoxelAccumulator(
            config["output_voxel_size_m"], config["max_output_voxels"]
        )
        for item in job["maps"]:
            points = load_xyz(item["pcd_path"], config["max_input_points_per_map"])
            matrix = transforms[item["map_id"]]
            for start in range(0, len(points), 200000):
                accumulator.add(_transform(points[start:start + 200000], matrix))
        fused_points = accumulator.points()
        if not len(fused_points):
            raise FusionError("fusion produced no points")

        for item in input_metadata:
            if _fingerprint(item["pcd_path"]) != item["sha256"]:
                raise FusionError("input map changed during fusion: %s" % item["map_id"])

        report = {
            "schema_version": 1,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "reference_map_id": job["reference_map_id"],
            "inputs": input_metadata,
            "T_reference_from_map": dict(
                (map_id, transforms[map_id].tolist()) for map_id in sorted(transforms)
            ),
            "placements": edge_reports,
            "output": {
                "pcd_path": job["output_pcd_path"],
                "point_count": int(len(fused_points)),
                "voxel_size_m": config["output_voxel_size_m"],
            },
        }

        temporary_pcd = _temporary_path(job["output_pcd_path"], ".pcd")
        temporary_report = _temporary_path(job["report_path"], ".json")
        installed_pcd = False
        installed_report = False
        try:
            write_binary_xyz(temporary_pcd, fused_points)
            with io.open(temporary_report, "w", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
            os.link(temporary_pcd, job["output_pcd_path"])
            installed_pcd = True
            os.link(temporary_report, job["report_path"])
            installed_report = True
        except OSError:
            if installed_report and os.path.exists(job["report_path"]):
                os.unlink(job["report_path"])
            if installed_pcd and os.path.exists(job["output_pcd_path"]):
                os.unlink(job["output_pcd_path"])
            raise
        finally:
            for path in (temporary_pcd, temporary_report):
                if os.path.exists(path):
                    os.unlink(path)
        return report
    except (PcdError, OSError) as exc:
        raise FusionError(str(exc))
