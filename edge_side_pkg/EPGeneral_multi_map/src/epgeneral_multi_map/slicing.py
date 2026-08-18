import threading
from dataclasses import dataclass, field

from .models import SliceBatch, SynchronizedFrame


class SliceError(ValueError):
    pass


@dataclass
class _SliceState:
    frames: list = field(default_factory=list)
    raw_points: int = 0
    raw_bytes: int = 0
    truncated: bool = False
    dropped_resource: int = 0


class SliceCollector:
    def __init__(self, start_at_ns, duration_ns, late_arrival_ns, limits):
        for value, name, minimum in (
            (start_at_ns, "start_at_ns", 0),
            (duration_ns, "duration_ns", 1),
            (late_arrival_ns, "late_arrival_ns", 0),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise SliceError("%s is invalid" % name)
        if not isinstance(limits, dict):
            raise SliceError("limits must be a mapping")
        self.limits = {}
        for key in ("max_slice_frames", "max_slice_points", "max_slice_bytes"):
            value = limits.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SliceError("%s is invalid" % key)
            self.limits[key] = value
        self.start_at_ns = start_at_ns
        self.duration_ns = duration_ns
        self.late_arrival_ns = late_arrival_ns
        self._states = {}
        self._next_seal_id = 0
        self._lock = threading.RLock()

    def slice_id_for(self, stamp_ns):
        if isinstance(stamp_ns, bool) or not isinstance(stamp_ns, int):
            raise SliceError("stamp_ns is invalid")
        if stamp_ns < self.start_at_ns:
            raise SliceError("stamp precedes start_at_ns")
        return (stamp_ns - self.start_at_ns) // self.duration_ns

    def window_for(self, slice_id):
        if isinstance(slice_id, bool) or not isinstance(slice_id, int) or slice_id < 0:
            raise SliceError("slice_id is invalid")
        start_ns = self.start_at_ns + slice_id * self.duration_ns
        return start_ns, start_ns + self.duration_ns

    def _batch(self, slice_id, state, end_ns=None, partial=False, error_tail=False):
        start_ns, window_end_ns = self.window_for(slice_id)
        state = state or _SliceState()
        return SliceBatch(
            slice_id, start_ns, window_end_ns if end_ns is None else end_ns,
            state.frames, partial=partial, error_tail=error_tail,
            truncated=state.truncated, dropped_resource=state.dropped_resource,
        )

    def add(self, frame):
        if not isinstance(frame, SynchronizedFrame):
            raise TypeError("frame must be SynchronizedFrame")
        slice_id = self.slice_id_for(frame.stamp_ns)
        with self._lock:
            if slice_id < self._next_seal_id:
                return "late"
            state = self._states.get(slice_id)
            if state is None:
                if len(self._states) >= 2:
                    raise SliceError("only two pending slice windows are allowed")
                state = _SliceState()
                self._states[slice_id] = state
            overflow = (
                len(state.frames) + 1 > self.limits["max_slice_frames"]
                or state.raw_points + frame.raw_point_count > self.limits["max_slice_points"]
                or state.raw_bytes + frame.raw_bytes > self.limits["max_slice_bytes"]
            )
            if state.truncated or overflow:
                state.truncated = True
                state.dropped_resource += 1
                return "truncated"
            state.frames.append(frame)
            state.raw_points += frame.raw_point_count
            state.raw_bytes += frame.raw_bytes
            return "accepted"

    def seal_ready(self, wall_time_ns):
        if isinstance(wall_time_ns, bool) or not isinstance(wall_time_ns, int):
            raise SliceError("wall_time_ns is invalid")
        batches = []
        with self._lock:
            while True:
                unused_start, end_ns = self.window_for(self._next_seal_id)
                if wall_time_ns < end_ns + self.late_arrival_ns:
                    break
                slice_id = self._next_seal_id
                self._next_seal_id += 1
                batches.append(self._batch(slice_id, self._states.pop(slice_id, None)))
        return batches

    def seal_tail(self, stop_at_ns, error_tail):
        if isinstance(stop_at_ns, bool) or not isinstance(stop_at_ns, int):
            raise SliceError("stop_at_ns is invalid")
        with self._lock:
            if stop_at_ns <= self.start_at_ns:
                self._states.clear()
                return None
            if (stop_at_ns - self.start_at_ns) % self.duration_ns == 0:
                self._states.clear()
                return None
            slice_id = self.slice_id_for(stop_at_ns - 1)
            state = self._states.get(slice_id) or _SliceState()
            state.frames[:] = [item for item in state.frames if item.stamp_ns < stop_at_ns]
            state.raw_points = sum(item.raw_point_count for item in state.frames)
            state.raw_bytes = sum(item.raw_bytes for item in state.frames)
            self._states.clear()
            self._next_seal_id = max(self._next_seal_id, slice_id + 1)
            return self._batch(
                slice_id, state, end_ns=stop_at_ns, partial=True,
                error_tail=bool(error_tail),
            )

    def pending_slice_ids(self):
        with self._lock:
            return tuple(sorted(self._states))

    def clear(self):
        with self._lock:
            self._states.clear()
