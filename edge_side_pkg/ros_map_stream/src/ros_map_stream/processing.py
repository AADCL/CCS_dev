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
