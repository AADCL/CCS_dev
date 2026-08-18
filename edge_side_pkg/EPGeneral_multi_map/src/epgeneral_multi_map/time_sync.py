import bisect
import math
import threading

import numpy as np

from .models import PoseMatch, PoseSample


def _normalized(value):
    array = np.asarray(value, dtype=np.float64)
    norm = float(np.linalg.norm(array))
    if array.shape != (4,) or not np.isfinite(array).all() or norm < 1e-12:
        raise ValueError("quaternion is invalid")
    return array / norm


def slerp_xyzw(left, right, ratio):
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("SLERP ratio must be between zero and one")
    q0 = _normalized(left)
    q1 = _normalized(right)
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    if dot > 0.9995:
        return _normalized(q0 + ratio * (q1 - q0))
    angle = math.acos(max(-1.0, min(1.0, dot)))
    denominator = math.sin(angle)
    scale0 = math.sin((1.0 - ratio) * angle) / denominator
    scale1 = math.sin(ratio * angle) / denominator
    return _normalized(scale0 * q0 + scale1 * q1)


def _rotation_matrix(quaternion):
    x, y, z, w = _normalized(quaternion)
    return np.asarray([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)


def _match(sample, error_ns):
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = _rotation_matrix(sample.rotation_xyzw)
    transform[:3, 3] = sample.translation
    return PoseMatch(transform, sample.stamp_ns, sample.stamp_ns, error_ns, False)


def interpolate_pose(before, after, stamp_ns):
    if after.stamp_ns <= before.stamp_ns or not before.stamp_ns <= stamp_ns <= after.stamp_ns:
        raise ValueError("pose interpolation timestamps are invalid")
    ratio = float(stamp_ns - before.stamp_ns) / float(after.stamp_ns - before.stamp_ns)
    translation = before.translation + ratio * (after.translation - before.translation)
    rotation = slerp_xyzw(before.rotation_xyzw, after.rotation_xyzw, ratio)
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = _rotation_matrix(rotation)
    transform[:3, 3] = translation
    return PoseMatch(
        transform, before.stamp_ns, after.stamp_ns,
        max(stamp_ns - before.stamp_ns, after.stamp_ns - stamp_ns), True,
    )


class PoseBuffer:
    def __init__(self, maximum):
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 2:
            raise ValueError("pose buffer maximum must be an integer >= 2")
        self.maximum = maximum
        self._samples = []
        self._lock = threading.RLock()

    def add(self, sample):
        if not isinstance(sample, PoseSample):
            raise TypeError("sample must be PoseSample")
        with self._lock:
            # ponytail: the configured cache is small; bisect plus a bounded list is enough.
            stamps = [item.stamp_ns for item in self._samples]
            index = bisect.bisect_left(stamps, sample.stamp_ns)
            if index < len(self._samples) and self._samples[index].stamp_ns == sample.stamp_ns:
                return False
            self._samples.insert(index, sample)
            if len(self._samples) > self.maximum:
                del self._samples[0]
            return True

    def match(self, stamp_ns, tolerance_ns):
        if isinstance(stamp_ns, bool) or not isinstance(stamp_ns, int) or stamp_ns < 0:
            raise ValueError("stamp_ns is invalid")
        if isinstance(tolerance_ns, bool) or not isinstance(tolerance_ns, int) or tolerance_ns < 0:
            raise ValueError("tolerance_ns is invalid")
        with self._lock:
            if not self._samples:
                return None
            stamps = [item.stamp_ns for item in self._samples]
            index = bisect.bisect_left(stamps, stamp_ns)
            if index < len(stamps) and stamps[index] == stamp_ns:
                return _match(self._samples[index], 0)
            before = self._samples[index - 1] if index else None
            after = self._samples[index] if index < len(self._samples) else None
            before_error = stamp_ns - before.stamp_ns if before is not None else None
            after_error = after.stamp_ns - stamp_ns if after is not None else None
            if (before is not None and after is not None
                    and before_error <= tolerance_ns and after_error <= tolerance_ns):
                return interpolate_pose(before, after, stamp_ns)
            candidates = [
                (error, item) for error, item in (
                    (before_error, before), (after_error, after),
                ) if item is not None and error <= tolerance_ns
            ]
            if not candidates:
                return None
            error, nearest = min(candidates, key=lambda item: (item[0], item[1].stamp_ns))
            return _match(nearest, error)

    def stamps(self):
        with self._lock:
            return tuple(item.stamp_ns for item in self._samples)

    def clear(self):
        with self._lock:
            self._samples[:] = []
