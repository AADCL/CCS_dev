import math

import numpy as np

from .models import PoseMatch, SynchronizedFrame


class ProcessingError(ValueError):
    pass


def read_path(value, path):
    current = value
    for part in path.split("."):
        if not part:
            raise ProcessingError("field path contains an empty segment")
        try:
            current = getattr(current, part)
        except AttributeError as exc:
            raise ProcessingError("field path is unavailable: %s" % path) from exc
    return current


def stamp_to_ns(stamp):
    try:
        if hasattr(stamp, "to_nsec"):
            return int(stamp.to_nsec())
        return int(stamp.secs) * 1_000_000_000 + int(stamp.nsecs)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProcessingError("ROS stamp is invalid: %s" % exc)


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


def extract_pointcloud2(message, reader=None):
    if reader is None:
        from sensor_msgs import point_cloud2
        reader = point_cloud2.read_points
    try:
        values = reader(message, field_names=("x", "y", "z"), skip_nans=False)
        flattened = np.fromiter(
            (coordinate for point in values for coordinate in point[:3]), dtype=np.float32
        )
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
    array = array[
        (distances_squared >= min_range_m * min_range_m)
        & (distances_squared <= max_range_m * max_range_m)
    ]
    if not len(array):
        return np.empty((0, 3), dtype="<f4")
    voxel_keys = np.floor(array / float(voxel_size_m)).astype(np.int64)
    unused_unique, indices = np.unique(voxel_keys, axis=0, return_index=True)
    array = array[np.sort(indices)]
    if len(array) > max_points:
        selected = np.linspace(0, len(array) - 1, max_points, dtype=np.int64)
        array = array[selected]
    return np.ascontiguousarray(array, dtype="<f4")


def synchronized_frame(message, stamp_ns, pose_match):
    if not isinstance(pose_match, PoseMatch):
        raise ProcessingError("pose_match must be PoseMatch")
    try:
        point_count = int(message.width) * int(message.height)
        raw_bytes = len(message.data)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProcessingError("PointCloud2 dimensions or data are invalid: %s" % exc)
    if point_count < 0:
        raise ProcessingError("PointCloud2 point count is invalid")
    return SynchronizedFrame(
        int(stamp_ns), message, point_count, raw_bytes,
        pose_match.transform.copy(), pose_match,
    )


def transform_to_payload(transform):
    matrix = np.asarray(transform, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ProcessingError("transform must be a finite 4x4 matrix")
    rotation = matrix[:3, :3]
    trace = float(np.trace(rotation))
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * scale
        qx = (rotation[2, 1] - rotation[1, 2]) / scale
        qy = (rotation[0, 2] - rotation[2, 0]) / scale
        qz = (rotation[1, 0] - rotation[0, 1]) / scale
    else:
        index = int(np.argmax(np.diag(rotation)))
        if index == 0:
            scale = math.sqrt(max(0.0, 1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2])) * 2.0
            qx = 0.25 * scale
            qy = (rotation[0, 1] + rotation[1, 0]) / scale
            qz = (rotation[0, 2] + rotation[2, 0]) / scale
            qw = (rotation[2, 1] - rotation[1, 2]) / scale
        elif index == 1:
            scale = math.sqrt(max(0.0, 1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2])) * 2.0
            qx = (rotation[0, 1] + rotation[1, 0]) / scale
            qy = 0.25 * scale
            qz = (rotation[1, 2] + rotation[2, 1]) / scale
            qw = (rotation[0, 2] - rotation[2, 0]) / scale
        else:
            scale = math.sqrt(max(0.0, 1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1])) * 2.0
            qx = (rotation[0, 2] + rotation[2, 0]) / scale
            qy = (rotation[1, 2] + rotation[2, 1]) / scale
            qz = 0.25 * scale
            qw = (rotation[1, 0] - rotation[0, 1]) / scale
    quaternion = np.asarray([qx, qy, qz, qw], dtype=np.float64)
    norm = float(np.linalg.norm(quaternion))
    if norm < 1e-12:
        raise ProcessingError("transform rotation is invalid")
    quaternion /= norm
    return {
        "x": float(matrix[0, 3]), "y": float(matrix[1, 3]), "z": float(matrix[2, 3]),
        "qx": float(quaternion[0]), "qy": float(quaternion[1]),
        "qz": float(quaternion[2]), "qw": float(quaternion[3]),
    }


class PassThroughSliceProcessor:
    def process(self, batch):
        return batch
