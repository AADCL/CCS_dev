import math
import threading
from collections import deque

import numpy as np


class ProcessingError(ValueError):
    pass


def read_path(value, path):
    current = value
    for part in path.split("."):
        if not part:
            raise ProcessingError("field path contains an empty segment")
        current = getattr(current, part)
    return current


def stamp_to_ns(stamp):
    if hasattr(stamp, "to_nsec"):
        return int(stamp.to_nsec())
    return int(stamp.secs) * 1000000000 + int(stamp.nsecs)


def transform_from_pose(message, position_path, orientation_path):
    try:
        position = read_path(message, position_path)
        orientation = read_path(message, orientation_path)
        result = {
            "x": float(position.x), "y": float(position.y), "z": float(position.z),
            "qx": float(orientation.x), "qy": float(orientation.y),
            "qz": float(orientation.z), "qw": float(orientation.w),
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProcessingError("pose fields are invalid: %s" % exc)
    if not all(math.isfinite(value) for value in result.values()):
        raise ProcessingError("pose contains non-finite values")
    norm = math.sqrt(sum(result[key] * result[key] for key in ("qx", "qy", "qz", "qw")))
    if norm < 1e-6:
        raise ProcessingError("pose quaternion is zero")
    for key in ("qx", "qy", "qz", "qw"):
        result[key] /= norm
    return result


class PoseSample(object):
    def __init__(self, stamp_ns, transform):
        self.stamp_ns = int(stamp_ns)
        self.transform = transform


class PoseBuffer(object):
    def __init__(self, maximum):
        self.maximum = int(maximum)
        self._items = deque(maxlen=self.maximum)
        self._lock = threading.Lock()

    def add(self, sample):
        with self._lock:
            self._items.append(sample)
            if len(self._items) > 1 and self._items[-2].stamp_ns > sample.stamp_ns:
                self._items = deque(sorted(self._items, key=lambda item: item.stamp_ns), maxlen=self.maximum)

    def closest(self, stamp_ns, tolerance_ns):
        with self._lock:
            if not self._items:
                return None
            selected = min(self._items, key=lambda item: abs(item.stamp_ns - stamp_ns))
            if abs(selected.stamp_ns - stamp_ns) > tolerance_ns:
                return None
            return selected

    def newest_stamp(self):
        with self._lock:
            if not self._items:
                return None
            return self._items[-1].stamp_ns

    def clear(self):
        with self._lock:
            self._items.clear()


def extract_pointcloud2(message, reader=None):
    if reader is None:
        from sensor_msgs import point_cloud2
        reader = point_cloud2.read_points
    try:
        values = reader(message, field_names=("x", "y", "z"), skip_nans=False)
        flattened = np.fromiter((coordinate for point in values for coordinate in point[:3]), dtype=np.float32)
    except Exception as exc:
        raise ProcessingError("PointCloud2 XYZ extraction failed: %s" % exc)
    if flattened.size % 3:
        raise ProcessingError("PointCloud2 XYZ data is incomplete")
    return flattened.reshape((-1, 3))


def preprocess_points(points, min_range_m, max_range_m, voxel_size_m, max_points):
    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ProcessingError("point cloud must be an Nx3 array")
    array = array[np.isfinite(array).all(axis=1)]
    if not len(array):
        return np.empty((0, 3), dtype="<f4")
    distances_squared = np.einsum("ij,ij->i", array, array)
    array = array[(distances_squared >= min_range_m * min_range_m) & (distances_squared <= max_range_m * max_range_m)]
    if not len(array):
        return np.empty((0, 3), dtype="<f4")
    voxel_keys = np.floor(array / float(voxel_size_m)).astype(np.int64)
    unused_unique, indices = np.unique(voxel_keys, axis=0, return_index=True)
    array = array[np.sort(indices)]
    if len(array) > max_points:
        selected = np.linspace(0, len(array) - 1, max_points, dtype=np.int64)
        array = array[selected]
    return np.ascontiguousarray(array, dtype="<f4")


def transform_matrix(transform):
    qx = float(transform["qx"])
    qy = float(transform["qy"])
    qz = float(transform["qz"])
    qw = float(transform["qw"])
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm < 1e-6:
        raise ProcessingError("transform quaternion is zero")
    qx, qy, qz, qw = qx / norm, qy / norm, qz / norm, qw / norm
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = np.asarray([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ], dtype=np.float64)
    matrix[:3, 3] = [float(transform["x"]), float(transform["y"]), float(transform["z"])]
    if not np.isfinite(matrix).all():
        raise ProcessingError("transform contains non-finite values")
    return matrix


def aggregate_window(scans, body_from_sensor, voxel_size_m, max_points):
    """Express every synchronized scan in the final scan's sensor frame."""
    if not scans:
        return np.empty((0, 3), dtype="<f4"), None
    reference_pose = scans[-1][1]
    sensor_from_reference = np.linalg.inv(
        np.dot(transform_matrix(reference_pose), transform_matrix(body_from_sensor)))
    transformed = []
    for points, map_from_body, unused_stamp in scans:
        array = np.asarray(points, dtype=np.float64)
        if not len(array):
            continue
        map_from_sensor = np.dot(transform_matrix(map_from_body), transform_matrix(body_from_sensor))
        reference_from_sensor = np.dot(sensor_from_reference, map_from_sensor)
        transformed.append(
            np.dot(array, reference_from_sensor[:3, :3].T) + reference_from_sensor[:3, 3])
    if not transformed:
        return np.empty((0, 3), dtype="<f4"), reference_pose
    combined = np.concatenate(transformed, axis=0)
    combined = combined[np.isfinite(combined).all(axis=1)]
    if not len(combined):
        return np.empty((0, 3), dtype="<f4"), reference_pose
    voxel_keys = np.floor(combined / float(voxel_size_m)).astype(np.int64)
    unused_unique, indices = np.unique(voxel_keys, axis=0, return_index=True)
    combined = combined[np.sort(indices)]
    if len(combined) > max_points:
        selected = np.linspace(0, len(combined) - 1, max_points, dtype=np.int64)
        combined = combined[selected]
    return np.ascontiguousarray(combined, dtype="<f4"), reference_pose


def map_points_to_sensor(points, map_from_body, body_from_sensor):
    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ProcessingError("point cloud must be an Nx3 array")
    map_from_sensor = np.dot(
        transform_matrix(map_from_body), transform_matrix(body_from_sensor))
    sensor_from_map = np.linalg.inv(map_from_sensor)
    converted = np.dot(array, sensor_from_map[:3, :3].T) + sensor_from_map[:3, 3]
    if not np.isfinite(converted).all():
        raise ProcessingError("registered cloud conversion produced non-finite points")
    return np.ascontiguousarray(converted, dtype="<f4")


def sensor_points_to_map(points, map_from_body, body_from_sensor):
    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ProcessingError("point cloud must be an Nx3 array")
    map_from_sensor = np.dot(
        transform_matrix(map_from_body), transform_matrix(body_from_sensor))
    converted = np.dot(array, map_from_sensor[:3, :3].T) + map_from_sensor[:3, 3]
    if not np.isfinite(converted).all():
        raise ProcessingError("preview cloud conversion produced non-finite points")
    return np.ascontiguousarray(converted, dtype="<f4")
