from __future__ import annotations

import copy
import json
import math
import os
import statistics
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .runtime_paths import application_root


DEFAULT_CONFIG_PATH = application_root() / "config" / "battery_estimation.json"
DEFAULT_HISTORY_ROOT = application_root() / "data" / "battery_history"
DEFAULT_PROFILE_PAYLOAD = {
    "schema_version": 2,
    "profiles": {
        "wheeltec_r550p": {
            "retention_days": 2,
            "sample_window": 15,
            "curve": [
                {"voltage": 20.0, "percentage": 0},
                {"voltage": 20.8, "percentage": 10},
                {"voltage": 21.5, "percentage": 25},
                {"voltage": 22.3, "percentage": 45},
                {"voltage": 23.1, "percentage": 65},
                {"voltage": 24.0, "percentage": 82},
                {"voltage": 24.8, "percentage": 94},
                {"voltage": 25.5, "percentage": 100},
            ],
        },
        "scout_mini": {
            "retention_days": 2,
            "sample_window": 15,
            "calibration_status": "待实测修正",
            "curve": [
                {"voltage": 24.5, "percentage": 0},
                {"voltage": 25.2, "percentage": 10},
                {"voltage": 25.9, "percentage": 25},
                {"voltage": 26.6, "percentage": 45},
                {"voltage": 27.3, "percentage": 65},
                {"voltage": 28.0, "percentage": 82},
                {"voltage": 28.7, "percentage": 94},
                {"voltage": 29.4, "percentage": 100},
            ],
        },
    },
}


@dataclass(frozen=True)
class BatteryProfile:
    retention_days: int
    sample_window: int
    curve: tuple[tuple[float, float], ...]


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _migrate_payload(config_path: Path, payload: dict) -> dict:
    if payload.get("schema_version") == 2:
        return payload
    if payload.get("schema_version") != 1:
        raise ValueError("电池估算配置必须使用 schema_version 1 或 2")
    migrated = copy.deepcopy(DEFAULT_PROFILE_PAYLOAD)
    legacy = payload.get("profiles", {}).get("scout_mini", {})
    candidate = legacy.get("curve") if isinstance(legacy, dict) else None
    if isinstance(candidate, list) and len(candidate) >= 2:
        try:
            curve = [
                {"voltage": float(item["voltage"]), "percentage": float(item["percentage"])}
                for item in candidate
            ]
            full_voltage = float(legacy.get("full_voltage", curve[-1]["voltage"]))
            if full_voltage > curve[-1]["voltage"] and curve[-1]["percentage"] < 100:
                curve.append({"voltage": full_voltage, "percentage": 100.0})
            elif curve[-1]["percentage"] < 100:
                curve[-1]["percentage"] = 100.0
            if all(
                curve[index]["voltage"] < curve[index + 1]["voltage"]
                and curve[index]["percentage"] < curve[index + 1]["percentage"]
                for index in range(len(curve) - 1)
            ):
                migrated["profiles"]["scout_mini"]["curve"] = curve
        except (KeyError, TypeError, ValueError):
            pass
    try:
        _write_json_atomic(config_path, migrated)
    except OSError:
        # The application-wide path validation reports unwritable config roots.
        pass
    return migrated


class BatteryEstimator:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH,
                 history_root: Path = DEFAULT_HISTORY_ROOT) -> None:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload = _migrate_payload(config_path, payload)
        if payload.get("schema_version") != 2 or not isinstance(payload.get("profiles"), dict):
            raise ValueError("电池估算配置必须使用 schema_version 2")
        self.profiles: dict[str, BatteryProfile] = {}
        for name, raw in payload["profiles"].items():
            curve = tuple(
                (float(item["voltage"]), float(item["percentage"]))
                for item in raw.get("curve", ())
            )
            invalid_curve = (
                len(curve) < 2
                or any(
                    curve[index][0] >= curve[index + 1][0]
                    or curve[index][1] >= curve[index + 1][1]
                    for index in range(len(curve) - 1)
                )
                or any(not math.isfinite(value) for point in curve for value in point)
                or curve[0][1] < 0
                or curve[-1][1] > 100
            )
            if invalid_curve:
                raise ValueError(f"{name} 电池曲线必须按电压和 0–100 电量严格递增")
            retention_days = int(raw.get("retention_days", 2))
            sample_window = int(raw.get("sample_window", 15))
            if retention_days < 1 or not 1 <= sample_window <= 600:
                raise ValueError(f"{name} 电池历史或采样窗口无效")
            self.profiles[str(name)] = BatteryProfile(
                retention_days, sample_window, curve,
            )
        self.history_root = history_root
        self._samples: dict[tuple[str, str], deque[float]] = {}

    def observe(self, device_id: str, profile_name: str, voltage: float | None,
                timestamp: datetime, online: bool) -> float | None:
        profile = self.profiles.get(profile_name)
        if profile is None or voltage is None:
            return None
        try:
            numeric = float(voltage)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric) or not 0 < numeric < 100:
            return None
        key = (device_id, profile_name)
        values = self._samples.get(key)
        if values is None or values.maxlen != profile.sample_window:
            values = deque(maxlen=profile.sample_window)
            self._samples[key] = values
        values.append(numeric)
        median_voltage = statistics.median(values)
        stamp = timestamp.astimezone(timezone.utc)
        minute = stamp.replace(second=0, microsecond=0).isoformat()
        self._write_history(
            device_id, profile_name, minute, median_voltage, online, stamp, profile,
        )
        return round(self.percentage(profile_name, median_voltage), 1)

    def percentage(self, profile_name: str, voltage: float) -> float:
        profile = self.profiles.get(profile_name)
        if profile is None:
            raise ValueError(f"未知电池 profile：{profile_name}")
        curve = profile.curve
        if voltage <= curve[0][0]:
            return max(0.0, curve[0][1])
        for (v0, p0), (v1, p1) in zip(curve, curve[1:]):
            if voltage <= v1:
                return max(0.0, min(
                    100.0, p0 + (voltage - v0) * (p1 - p0) / (v1 - v0),
                ))
        return min(100.0, curve[-1][1])

    def _write_history(self, device_id: str, profile_name: str, minute: str,
                       voltage: float, online: bool, now: datetime,
                       profile: BatteryProfile) -> None:
        self.history_root.mkdir(parents=True, exist_ok=True)
        path = self.history_root / f"{device_id}.json"
        try:
            records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except (OSError, json.JSONDecodeError):
            records = []
        cutoff = now - timedelta(days=profile.retention_days)
        records = [
            item for item in records
            if datetime.fromisoformat(item["minute"]) >= cutoff
        ]
        record = {
            "minute": minute, "profile": profile_name,
            "voltage_median": round(voltage, 4), "online": bool(online),
        }
        if records and records[-1].get("minute") == minute:
            records[-1] = record
        else:
            records.append(record)
        _write_json_atomic(path, records)
