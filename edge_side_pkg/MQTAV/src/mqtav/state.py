"""Python 3.6 compatible, thread-safe MAVROS health snapshots."""

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_percentage(value):
    """Convert BatteryState's 0..1 value to percentage points."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    if numeric <= 1:
        numeric *= 100
    return round(min(numeric, 100.0), 2)


class HealthState(object):
    def __init__(self, device):
        self._device = device
        self._lock = Lock()
        self._sequence = 0
        self._health = {
            "fcu_connected": None,
            "armed": None,
            "system_status": None,
            "flight_mode": "unknown",
            "battery": {"percentage": None, "voltage": None, "current": None},
            "mission_status": "unknown",
        }

    def update_state(self, connected, armed, system_status, mode):
        def optional_bool(value):
            return None if value is None else bool(value)

        with self._lock:
            self._health.update(
                fcu_connected=optional_bool(connected),
                armed=optional_bool(armed),
                system_status=system_status if system_status is not None else None,
                flight_mode=str(mode) if mode else "unknown",
            )

    def update_battery(self, percentage, voltage, current):
        def number(value):
            try:
                return round(float(value), 3)
            except (TypeError, ValueError):
                return None

        with self._lock:
            self._health["battery"] = {
                "percentage": normalize_percentage(percentage),
                "voltage": number(voltage),
                "current": number(current),
            }

    def update_mission(self, status):
        with self._lock:
            self._health["mission_status"] = "unknown" if status is None or str(status) == "" else str(status)

    def payload(self, message_type):
        with self._lock:
            self._sequence += 1
            return {
                "schema_version": "1.0",
                "message_type": message_type,
                "timestamp": utc_timestamp(),
                "sequence": self._sequence,
                "device": {"id": self._device.device_id, "ip": self._device.ip_address},
                "health": deepcopy(self._health),
            }

    def presence_payload(self, status):
        return {
            "schema_version": "1.0",
            "message_type": "presence",
            "timestamp": utc_timestamp(),
            "device": {"id": self._device.device_id, "ip": self._device.ip_address},
            "status": status,
        }
