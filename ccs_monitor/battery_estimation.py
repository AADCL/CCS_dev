from __future__ import annotations

import json
import os
import statistics
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from .runtime_paths import application_root
from pathlib import Path


DEFAULT_CONFIG_PATH = application_root() / "config" / "battery_estimation.json"
DEFAULT_HISTORY_ROOT = application_root() / "data" / "battery_history"


@dataclass(frozen=True)
class BatteryProfile:
    full_voltage: float
    retention_days: int
    curve: tuple[tuple[float, float], ...]


class BatteryEstimator:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH,
                 history_root: Path = DEFAULT_HISTORY_ROOT) -> None:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        raw = payload["profiles"]["scout_mini"]
        curve = tuple((float(item["voltage"]), float(item["percentage"])) for item in raw["curve"])
        if curve and (len(curve) < 2 or any(curve[i] >= curve[i + 1] for i in range(len(curve) - 1))):
            raise ValueError("Scout 电池曲线必须按电压和电量严格递增")
        self.profile = BatteryProfile(float(raw["full_voltage"]), int(raw["retention_days"]), curve)
        self.history_root = history_root
        self._minute_samples: dict[tuple[str, str], list[float]] = {}

    def observe(self, device_id: str, profile_name: str, voltage: float | None,
                timestamp: datetime, online: bool) -> float | None:
        if profile_name != "scout_mini" or voltage is None or not 0 < voltage < 100:
            return None
        stamp = timestamp.astimezone(timezone.utc)
        minute = stamp.replace(second=0, microsecond=0).isoformat()
        key = (device_id, minute)
        values = self._minute_samples.setdefault(key, [])
        values.append(float(voltage))
        self._write_history(device_id, minute, statistics.median(values), online, stamp)
        return self.percentage(voltage)

    def percentage(self, voltage: float) -> float | None:
        if voltage >= self.profile.full_voltage:
            return 100.0
        curve = self.profile.curve
        if not curve:
            return None
        if voltage <= curve[0][0]:
            return max(0.0, curve[0][1])
        for (v0, p0), (v1, p1) in zip(curve, curve[1:]):
            if voltage <= v1:
                return max(0.0, min(100.0, p0 + (voltage - v0) * (p1 - p0) / (v1 - v0)))
        return min(100.0, curve[-1][1])

    def _write_history(self, device_id: str, minute: str, voltage: float,
                       online: bool, now: datetime) -> None:
        self.history_root.mkdir(parents=True, exist_ok=True)
        path = self.history_root / f"{device_id}.json"
        try:
            records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (OSError, json.JSONDecodeError):
            records = []
        cutoff = now - timedelta(days=self.profile.retention_days)
        records = [item for item in records if datetime.fromisoformat(item["minute"]) >= cutoff]
        record = {"minute": minute, "voltage_median": round(voltage, 4), "online": bool(online)}
        if records and records[-1].get("minute") == minute:
            records[-1] = record
        else:
            records.append(record)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.history_root,
                                             prefix=f".{device_id}.", suffix=".tmp", delete=False) as handle:
                json.dump(records, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)
