import hashlib


DEVICE_COLORS = (
    "#22C55E", "#F59E0B", "#06B6D4", "#EF4444",
    "#8B5CF6", "#EC4899", "#84CC16", "#0EA5E9",
)


def device_display_color(device_id: str) -> str:
    """Return a stable, high-contrast color for a device ID."""
    digest = hashlib.sha256(device_id.strip().casefold().encode("utf-8")).digest()
    return DEVICE_COLORS[int.from_bytes(digest[:2], "big") % len(DEVICE_COLORS)]
