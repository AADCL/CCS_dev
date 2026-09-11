"""Isolated 1/10/20-device Qt/VisPy CPU replay; never opens device sockets.

The scene graph and mesh code are real; no GPU rasterization is measured.
Example: python scripts/benchmark_multi_device.py --devices 20 --seconds 600 --output build/performance/after-20.json
"""
import argparse
from array import array
import ctypes
import json
import math
import os
import queue
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--devices", type=int, choices=(1, 10, 20), default=20)
parser.add_argument("--seconds", type=float, default=600)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.source_root.resolve()))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer, QSettings
from PySide6.QtWidgets import QApplication
from vispy import scene
from ccs_monitor.battery_estimation import BatteryEstimator
from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.models import DeviceProfile, DeviceMapBinding, FrameTransform, MapDefinition, MapStatus, MapCreatorDevice, utc_now
from ccs_monitor.mqtt_config import default_mqtt_config
from ccs_monitor.mqtt_data_source import MqttDeviceSource
from ccs_monitor.udp_config import load_udp_config
from ccs_monitor.udp_protocol import UdpEnvelope, UdpTelemetryProtocol
from ccs_monitor.udp_store import UdpTelemetryStore
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.task_repository import TaskRepository
from ccs_monitor.task_models import TaskWaypoint
from ccs_monitor.pages.map_page import PointCloudViewer
from ccs_monitor.main_window import MainWindow
from ccs_monitor.widgets import DeviceCard
from dataclasses import replace


def rss():
    if os.name == "nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak", "working", "peak_paged", "paged",
                                                    "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]
        value = Counters()
        value.cb = ctypes.sizeof(value)
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi")
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb)
        return value.working
    return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")


counters = dict(cards=0, meshes=0, renders=0, hidden_renders=0)
measuring = False
original_card_init = DeviceCard.__init__


def card_init(self, *a, **kw):
    if measuring:
        counters["cards"] += 1
    original_card_init(self, *a, **kw)


class CpuSceneViewer(PointCloudViewer):
    def __init__(self, *a, **kw):
        super().__init__(canvas_factory=lambda: SimpleNamespace(native=None))
        self._view = SimpleNamespace(scene=scene.Node())
        self._points_visual = scene.visuals.Markers(parent=self._view.scene)
        self._marker_visual = scene.visuals.Markers(parent=self._view.scene)
        self._device_axis_visual = scene.visuals.Line(parent=self._view.scene)
        self._trail_visual = scene.visuals.Line(parent=self._view.scene)
        self._conflict_visual = scene.visuals.Markers(parent=self._view.scene)
        self._map_axis_visual = scene.visuals.Line(parent=self._view.scene)

    def _create_marker_mesh(self, marker):
        if measuring:
            counters["meshes"] += 1
        return super()._create_marker_mesh(marker)

    def _render_markers(self):
        if measuring:
            counters["renders"] += 1
            if not self.isVisible():
                counters["hidden_renders"] += 1
        return super()._render_markers()


app = QApplication.instance() or QApplication([])
temporary = tempfile.TemporaryDirectory()
root = Path(temporary.name)
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(root / "settings"))
profiles = []
for i in range(args.devices):
    binding = DeviceMapBinding("bench-map", "map", "odom", FrameTransform(i * 10, 0, 0, 0, 0, 0, 1), utc_now())
    profiles.append(DeviceProfile(f"BENCH-{i:03}", f"模拟设备 {i:02}", "UGV", f"127.0.1.{i + 1}",
        active_map_id="bench-map", map_bindings=(binding,), battery_profile="wheeltec_r550p"))
config = DeviceConfigRepository(root / "devices.json")
config._write(profiles)
battery_config = root / "battery.json"
battery_config.write_bytes((args.source_root / "config/battery_estimation.json").read_bytes())
battery = BatteryEstimator(battery_config, root / "battery_history")
battery.history_root.mkdir()
stamp = utc_now().replace(second=0, microsecond=0)
history = [dict(minute=(stamp - timedelta(minutes=i)).isoformat(), profile="wheeltec_r550p",
                voltage_median=24.0, online=True) for i in reversed(range(2880))]
for profile in profiles:
    (battery.history_root / f"{profile.device_id}.json").write_text(json.dumps(history), encoding="utf-8")
source = MqttDeviceSource(default_mqtt_config(), config, battery_estimator=battery, start_watchdog=False)
udp_config = load_udp_config(args.source_root / "config/udp_telemetry.json")
udp = UdpTelemetryStore(udp_config, source.has_device_id, source.append_external_log, start_watchdog=False)
protocol = UdpTelemetryProtocol(udp_config)
maps = MapRepository(root / "maps")
map_dir = root / "maps" / "map"
map_dir.mkdir()
pcd = map_dir / "cloud.pcd"
pcd.write_text("VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH 1000\nHEIGHT 1\nPOINTS 1000\nDATA ascii\n" +
               "\n".join(f"{i % 100} {i // 100} 0" for i in range(1000)), encoding="ascii")
definition = MapDefinition("bench-map", "Benchmark", status=MapStatus.READY, pcd_path="cloud.pcd",
    directory_name="map", creator_devices=tuple(MapCreatorDevice(item.device_id, item.device_name, item.device_type) for item in profiles))
maps._maps = [definition]
maps._active_map_id = definition.map_id
tasks = TaskRepository(root / "tasks")


def mqtt_message(profile, kind, sequence):
    payload = dict(schema_version="1.0", message_type=kind, timestamp=utc_now().isoformat(),
        device=dict(id=profile.device_id, ip=profile.ip_address), sequence=sequence, session_id="benchmark")
    if kind == "status":
        payload["health"] = dict(fcu_connected=True, armed=False, flight_mode="BENCHMARK",
            battery=dict(voltage=24.0 + math.sin(sequence) * 0.1), mission_status="standby")
    return f"mqtav/{profile.device_id}/{kind}", json.dumps(payload).encode()


for profile in profiles:
    source.process_message(*mqtt_message(profile, "heartbeat", 0))
task = tasks.create("Benchmark", definition, source.snapshots())
for i, subtask in enumerate(task.subtasks):
    tasks.update_subtask(task.task_id, replace(subtask,
        waypoints=(TaskWaypoint("a", i * 20, 0, 0), TaskWaypoint("b", i * 20 + 5, 0, 0))))
trajectory = None
if (args.source_root / "ccs_monitor/trajectory_store.py").is_file():
    from ccs_monitor.trajectory_store import DeviceTrajectoryStore
    trajectory = DeviceTrajectoryStore(source, udp, root=root / "trajectories")
patchers = [patch("ccs_monitor.pages.map_page.PointCloudViewer", CpuSceneViewer),
            patch("ccs_monitor.pages.task_page.PointCloudViewer", CpuSceneViewer),
            patch("ccs_monitor.pages.command_dashboard_page.PointCloudViewer", CpuSceneViewer),
            patch.object(DeviceCard, "__init__", card_init)]
for patcher in patchers:
    patcher.start()
extra = {"trajectory_store": trajectory} if trajectory is not None else {}
window = MainWindow(source, telemetry_store=udp, map_repository=maps, task_repository=tasks, **extra)
window.task_page.editor.set_task(tasks.task_by_id(task.task_id))
window.task_page.stack.setCurrentWidget(window.task_page.editor)
window.show()
window.set_current_page(3)
app.processEvents()
mailbox = queue.Queue(maxsize=20000)
stop = threading.Event()
generated = {"mqtt": 0, "udp": 0, "dropped": 0}
processed = {"mqtt": 0, "udp": 0}
delays, ui_delays, rss_samples = array("d"), array("d"), []
started = time.perf_counter()
cpu_started = time.process_time()
measuring = True


def enqueue(kind, payload):
    generated[kind] += 1
    try:
        mailbox.put_nowait((time.perf_counter(), kind, payload))
    except queue.Full:
        generated["dropped"] += 1


def produce():
    tick = 0
    while not stop.is_set():
        due = started + tick * 0.05
        if due - started >= args.seconds:
            return
        stop.wait(max(0, due - time.perf_counter()))
        for i, profile in enumerate(profiles):
            if tick % 20 == 0:
                for kind in ("heartbeat", "status"):
                    enqueue("mqtt", mqtt_message(profile, kind, tick + 1))
            levels = [1] + ([2] if tick % 4 == 0 else []) + ([3] if tick % 20 == 0 else [])
            for level in levels:
                fields = {}
                for descriptor in udp_config.descriptors:
                    if descriptor.level != level:
                        continue
                    data = dict(valid=True, sample_age_seconds=0.0)
                    if descriptor.data_type == "pose":
                        data.update(x=tick * 0.03, y=math.sin(tick / 30), z=0, roll=0, pitch=0, yaw=0)
                    elif descriptor.data_type == "imu":
                        data.update({key: 0.0 for key in ("roll", "pitch", "yaw", "angular_velocity_x", "angular_velocity_y",
                            "angular_velocity_z", "linear_acceleration_x", "linear_acceleration_y", "linear_acceleration_z")})
                    else:
                        data["status"] = "available"
                    fields[descriptor.name] = data
                frame = UdpEnvelope(profile.device_id, "benchmark", "telemetry", tick + 1, time.time_ns(), level, fields)
                enqueue("udp", protocol.encode(frame))
            if tick % 20 == 0:
                enqueue("udp", protocol.encode(UdpEnvelope(profile.device_id, "benchmark", "heartbeat", tick + 1, time.time_ns(), None, {})))
        tick += 1


producer = threading.Thread(target=produce, daemon=True)
producer.start()
last_ui = [time.perf_counter()]
last_page = [3]


def consume():
    deadline = time.perf_counter() + 0.004
    while time.perf_counter() < deadline:
        try:
            created, kind, payload = mailbox.get_nowait()
        except queue.Empty:
            break
        delays.append((time.perf_counter() - created) * 1000)
        if kind == "mqtt":
            source.process_message(*payload)
        else:
            udp.process_datagram(payload, "127.0.0.1", 0)
        processed[kind] += 1


def pulse():
    now = time.perf_counter()
    ui_delays.append(max(0, (now - last_ui[0] - 0.01) * 1000))
    last_ui[0] = now
    elapsed = now - started
    target = (3, 4, 1)[int(elapsed // 30) % 3]
    if target != last_page[0]:
        window.set_current_page(target)
        last_page[0] = target
    if elapsed >= args.seconds and (mailbox.empty() or elapsed >= args.seconds + 10):
        app.quit()


def sample():
    elapsed = time.perf_counter() - started
    rss_samples.append((elapsed, rss()))
    if int(elapsed) % 60 == 0:
        print(json.dumps(dict(elapsed=round(elapsed), queue=mailbox.qsize(), rss_mib=round(rss_samples[-1][1] / 1048576, 1))), flush=True)


timers = []
for interval, callback in ((1, consume), (10, pulse), (1000, sample)):
    timer = QTimer()
    timer.timeout.connect(callback)
    timer.start(interval)
    timers.append(timer)
app.exec()
stop.set()
producer.join(timeout=2)
for timer in timers:
    timer.stop()
elapsed = time.perf_counter() - started
cpu = (time.process_time() - cpu_started) / elapsed * 100
measuring = False
window.close()
if trajectory:
    trajectory.close()
if hasattr(battery, "close"):
    battery.close()


def percentile(values, quantile):
    return sorted(values)[min(len(values) - 1, int(len(values) * quantile))] if values else None


warm = [value for seconds, value in rss_samples if seconds >= min(120, args.seconds / 3)]
growth = max(0, max(warm) - warm[0]) if warm else 0
result = dict(source_root=str(args.source_root), devices=args.devices, seconds=round(elapsed, 2),
    measurement="Qt event loop and real VisPy CPU scene/mesh; GPU rasterization excluded",
    rates=dict(mqtt_heartbeat_hz=1, mqtt_status_hz=1, udp_levels_hz=[20, 5, 1], udp_heartbeat_hz=1),
    generated=generated, processed=processed, remaining_queue=mailbox.qsize(),
    ui_delay_ms=dict(p95=percentile(ui_delays, .95), p99=percentile(ui_delays, .99), maximum=max(ui_delays)),
    dispatch_delay_ms=dict(p95=percentile(delays, .95), p99=percentile(delays, .99)),
    cpu_percent=round(cpu, 2), rss_peak_mib=max(value for _, value in rss_samples) / 1048576 if rss_samples else rss() / 1048576,
    rss_growth_after_warmup_mib=growth / 1048576, rss_samples_mib=[(round(t, 2), v / 1048576) for t, v in rss_samples], counts=counters,
    acceptance=dict(ui=percentile(ui_delays, .95) <= 100 and percentile(ui_delays, .99) <= 250,
        memory=growth <= max(32 * 1048576, warm[0] * .1) if warm else True,
        no_drops=generated["dropped"] == 0 and mailbox.empty(), hidden_rendering=counters["hidden_renders"] == 0))
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False), flush=True)
for patcher in reversed(patchers):
    patcher.stop()
window.deleteLater()
app.processEvents()
temporary.cleanup()
