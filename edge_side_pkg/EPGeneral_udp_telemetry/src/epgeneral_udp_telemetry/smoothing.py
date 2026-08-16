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
        self.lock = threading.Lock()

    def add(self, sample, now):
        with self.lock:
            self.samples.append(sample)
            self.last_sample_time = now
            self.event_times.append(now)

    def touch(self, now):
        with self.lock:
            self.last_sample_time = now
            self.event_times.append(now)

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
                self.last_output = self._average(self.samples, data_type)
                self.samples = []
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
        return result
