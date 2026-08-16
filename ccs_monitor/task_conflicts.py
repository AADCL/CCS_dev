from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

from .task_models import DeviceSubtask, TaskConflict, TaskSafetySettings


@dataclass(frozen=True)
class _TimedSegment:
    device_id: str
    index: int
    start: float
    end: float
    p0: np.ndarray
    p1: np.ndarray

    def position(self, timestamp: float) -> np.ndarray:
        if self.end <= self.start:
            return self.p1
        ratio = min(1.0, max(0.0, (timestamp - self.start) / (self.end - self.start)))
        return self.p0 + (self.p1 - self.p0) * ratio


class TaskConflictDetector:
    def detect(
        self,
        subtasks: tuple[DeviceSubtask, ...] | list[DeviceSubtask],
        settings: TaskSafetySettings,
    ) -> tuple[TaskConflict, ...]:
        tracks = {item.device_id: self._segments(item) for item in subtasks if item.is_valid}
        conflicts: list[TaskConflict] = []
        device_ids = sorted(tracks)
        for first_index, first_id in enumerate(device_ids):
            for second_id in device_ids[first_index + 1:]:
                for first in tracks[first_id]:
                    for second in tracks[second_id]:
                        conflict = self._segment_conflict(first, second, settings)
                        if conflict is not None:
                            conflicts.append(conflict)
        conflicts.sort(key=lambda item: (item.time_seconds, item.conflict_id))
        return tuple(conflicts)

    @staticmethod
    def _segments(subtask: DeviceSubtask) -> tuple[_TimedSegment, ...]:
        timestamp = float(subtask.start_delay_seconds)
        result: list[_TimedSegment] = []
        for index, (first, second) in enumerate(zip(subtask.waypoints, subtask.waypoints[1:])):
            p0 = np.asarray((first.x, first.y, first.z), dtype=np.float64)
            p1 = np.asarray((second.x, second.y, second.z), dtype=np.float64)
            duration = float(np.linalg.norm(p1 - p0)) / subtask.cruise_speed_mps
            end = timestamp + duration
            result.append(_TimedSegment(subtask.device_id, index, timestamp, end, p0, p1))
            timestamp = end
        return tuple(result)

    def _segment_conflict(
        self,
        first: _TimedSegment,
        second: _TimedSegment,
        settings: TaskSafetySettings,
    ) -> TaskConflict | None:
        best: tuple[float, float, float, np.ndarray, np.ndarray] | None = None
        for offset in (-settings.time_margin_seconds, 0.0, settings.time_margin_seconds):
            low = max(first.start, second.start - offset)
            high = min(first.end, second.end - offset)
            if high < low:
                continue
            candidates = [low, high]
            first_velocity = self._velocity(first)
            second_velocity = self._velocity(second)
            relative_velocity = first_velocity - second_velocity
            first_base = first.position(low)
            second_base = second.position(low + offset)
            relative = first_base - second_base
            denominator = float(np.dot(relative_velocity[:2], relative_velocity[:2]))
            if denominator > 1e-12:
                closest = low - float(np.dot(relative[:2], relative_velocity[:2])) / denominator
                candidates.append(min(high, max(low, closest)))
            if abs(relative_velocity[2]) > 1e-12:
                z_crossing = low - relative[2] / relative_velocity[2]
                candidates.append(min(high, max(low, z_crossing)))
            for timestamp in candidates:
                p1 = first.position(timestamp)
                p2 = second.position(timestamp + offset)
                horizontal = float(np.linalg.norm((p1 - p2)[:2]))
                vertical = abs(float(p1[2] - p2[2]))
                if horizontal <= settings.horizontal_distance_m and vertical <= settings.vertical_distance_m:
                    score = horizontal / settings.horizontal_distance_m + vertical / settings.vertical_distance_m
                    candidate = (score, timestamp, horizontal, p1, p2)
                    if best is None or candidate[0] < best[0]:
                        best = candidate
        if best is None:
            return None
        _, timestamp, horizontal, p1, p2 = best
        vertical = abs(float(p1[2] - p2[2]))
        midpoint = (p1 + p2) / 2.0
        identity = (
            f"{first.device_id}:{first.index}:{second.device_id}:{second.index}:"
            f"{timestamp:.3f}"
        )
        return TaskConflict(
            hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16],
            first.device_id,
            second.device_id,
            first.index,
            second.index,
            timestamp,
            horizontal,
            vertical,
            float(midpoint[0]),
            float(midpoint[1]),
            float(midpoint[2]),
        )

    @staticmethod
    def _velocity(segment: _TimedSegment) -> np.ndarray:
        duration = segment.end - segment.start
        return np.zeros(3, dtype=np.float64) if duration <= 0 else (segment.p1 - segment.p0) / duration

