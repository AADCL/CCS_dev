"""Application pages."""

from .devices_page import DevicesPage
from .device_detail_page import DeviceDetailPage
from .home_page import HomePage
from .map_page import MapPage
from .task_page import TaskPage
from .placeholder_page import PlaceholderPage
from .command_dashboard_page import (
    CollapsibleConsolePanel,
    CollapsibleDevicePanel,
    CommandDashboardPage,
    DevicePanelMode,
)

__all__ = [
    "CollapsibleConsolePanel", "CollapsibleDevicePanel", "CommandDashboardPage",
    "DeviceDetailPage", "DevicePanelMode", "DevicesPage", "HomePage", "MapPage",
    "PlaceholderPage", "TaskPage",
]
