import math
import threading
from collections import deque


def read_path(value, path):
    current = value
    for part in path.split("."):
        current = getattr(current, part)
    return current


def quaternion_to_euler_degrees(x, y, z, w):
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr, cosr)
    sinp = 2.0 * (w * y - z * x)
    pitch = math.copysign(math.pi / 2.0, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)
    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny, cosy)
    return tuple(math.degrees(value) for value in (roll, pitch, yaw))


def average_quaternions(values):
    if not values:
        return None
    reference = values[0]
    totals = [0.0, 0.0, 0.0, 0.0]
    for quaternion in values:
        dot = sum(a * b for a, b in zip(reference, quaternion))
        aligned = quaternion if dot >= 0.0 else tuple(-item for item in quaternion)
        for index, item in enumerate(aligned):
            totals[index] += item
    norm = math.sqrt(sum(item * item for item in totals))
    if norm <= 1e-12:
        return reference
    return tuple(item / norm for item in totals)


class TelemetrySampler(object):
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.samples = []
        self.last_output = None
        self.last_sample_time = None
        self.event_times = deque(maxlen=50)
        self.received_count = 0
        self.accepted_count = 0
        self.rejected_count = 0
        self.last_rejection_reason = ""
        self.lock = threading.Lock()

    def add(self, sample, now):
        with self.lock:
            self.received_count += 1
            reason = self._validation_error(sample)
            if reason:
                self.rejected_count += 1
                self.last_rejection_reason = reason
                return False
            self.samples.append(sample)
            self.last_sample_time = now
            self.event_times.append(now)
            self.accepted_count += 1
            return True

    def touch(self, now):
        with self.lock:
            self.received_count += 1
            self.accepted_count += 1
            self.last_sample_time = now
            self.event_times.append(now)

    def reject(self, reason, received=True):
        with self.lock:
            if received:
                self.received_count += 1
            self.rejected_count += 1
            self.last_rejection_reason = str(reason)

    def statistics(self, now):
        with self.lock:
            age = None if self.last_sample_time is None else max(0.0, now - self.last_sample_time)
            return {
                "received_count": self.received_count,
                "accepted_count": self.accepted_count,
                "rejected_count": self.rejected_count,
                "last_sample_age_seconds": age,
                "last_rejection_reason": self.last_rejection_reason,
            }

    def snapshot(self, now):
        with self.lock:
            data_type = self.descriptor["type"]
            if data_type == "availability":
                return self._availability(now)
            if data_type == "pointcloud_status":
                return self._pointcloud(now)
            if data_type == "text_status":
                return self._text_status(now)
            if self.samples:
                samples = self.samples
                self.samples = []
                self.last_output = self._average(samples, data_type)
            if self.last_output is None:
                return {"valid": False, "sample_age_seconds": None}
            output = dict(self.last_output)
            output["valid"] = True
            output["sample_age_seconds"] = max(0.0, now - self.last_sample_time)
            return output

    def _availability(self, now):
        timeout = float(self.descriptor["source"].get("timeout_seconds", 3.0))
        if self.last_sample_time is None:
            return {"valid": False, "status": "unknown", "sample_age_seconds": None}
        age = max(0.0, now - self.last_sample_time)
        return {"valid": True, "status": "available" if age <= timeout else "unavailable", "sample_age_seconds": age}

    def _pointcloud(self, now):
        timeout = float(self.descriptor["source"].get("timeout_seconds", 1.0))
        if self.last_sample_time is None:
            return {"valid": False, "status": "unknown", "estimated_hz": None, "sample_age_seconds": None}
        age = max(0.0, now - self.last_sample_time)
        rate = None
        if len(self.event_times) >= 2 and self.event_times[-1] > self.event_times[0]:
            rate = (len(self.event_times) - 1) / (self.event_times[-1] - self.event_times[0])
        return {"valid": True, "status": "available" if age <= timeout else "unavailable", "estimated_hz": rate, "sample_age_seconds": age}

    def _text_status(self, now):
        timeout = float(self.descriptor["source"].get("timeout_seconds", 3.0))
        if self.samples:
            self.last_output = self.samples[-1]
            self.samples = []
        if self.last_output is None or self.last_sample_time is None:
            return {"valid": False, "status": "unknown", "value": None, "sample_age_seconds": None}
        age = max(0.0, now - self.last_sample_time)
        return {
            "valid": True,
            "status": "available" if age <= timeout else "unavailable",
            "value": self.last_output["value"],
            "sample_age_seconds": age,
        }

    @staticmethod
    def _average(samples, data_type):
        numeric_fields = [key for key in samples[0] if key != "quaternion"]
        result = {key: sum(sample[key] for sample in samples) / len(samples) for key in numeric_fields}
        quaternion = average_quaternions([sample["quaternion"] for sample in samples])
        result["roll"], result["pitch"], result["yaw"] = quaternion_to_euler_degrees(*quaternion)
        if any(not math.isfinite(float(value)) for value in result.values()):
            raise ValueError("averaged sample contains NaN or infinity")
        return result

    def _validation_error(self, sample):
        if not isinstance(sample, dict):
            return "sample must be a mapping"
        if self.descriptor["type"] not in {"pose", "imu"}:
            return ""
        required_fields = (
            ("x", "y", "z") if self.descriptor["type"] == "pose" else
            (
                "angular_velocity_x", "angular_velocity_y", "angular_velocity_z",
                "linear_acceleration_x", "linear_acceleration_y", "linear_acceleration_z",
            )
        )
        missing_fields = [name for name in required_fields if name not in sample]
        if missing_fields:
            return "sample is missing fields: %s" % ", ".join(missing_fields)
        quaternion = sample.get("quaternion")
        if not isinstance(quaternion, (tuple, list)) or len(quaternion) != 4:
            return "quaternion must contain four values"
        numeric_values = [value for key, value in sample.items() if key != "quaternion"]
        numeric_values.extend(quaternion)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in numeric_values):
            return "sample contains a non-numeric value"
        if any(not math.isfinite(float(value)) for value in numeric_values):
            return "sample contains NaN or infinity"
        norm = math.sqrt(sum(float(value) * float(value) for value in quaternion))
        if not math.isfinite(norm) or norm <= 1e-12:
            return "quaternion norm is invalid"
        return ""
