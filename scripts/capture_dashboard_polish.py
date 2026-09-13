"""Capture native Windows OpenGL using temporary, synthetic maps and tasks.

Usage: python scripts/capture_dashboard_polish.py REPO OUTPUT [--baseline]
The baseline option accepts the main checkout without the task-overlay fix.
No network transport or execution service is constructed.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from unittest.mock import patch

repo = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
baseline = '--baseline' in sys.argv
sys.path.insert(0, str(repo))
os.environ['QT_QPA_PLATFORM'] = 'windows'

import numpy as np
from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtWidgets import QApplication

from ccs_monitor.app import configure_application_font
from ccs_monitor.data_source import SimulatedDeviceSource, simulated_overview
from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.device_types import DeviceTypeTemplateRepository
from ccs_monitor.main_window import MainWindow
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.models import ConnectionStatus, DeviceTelemetrySnapshot, HealthStatus, PoseTelemetry
from ccs_monitor.pages.map_page import MapViewerSettings
from ccs_monitor.styles import ThemeMode
from ccs_monitor.task_models import TaskWaypoint
from ccs_monitor.task_repository import TaskRepository
from tests.test_point_cloud import write_ascii_pcd


def events(seconds=0.25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def check_geometry(window, page):
    assert abs(page.dashboard_title.mapTo(page.top_bar, page.dashboard_title.rect().center()).x()
               - page.top_bar.rect().center().x()) <= 2
    controls = [page.map_combo, page.task_combo, page.start_button, page.stop_button,
                page.fit_button, page.reset_button, page.fullscreen_button,
                page.viewer.grid_spacing_input, page.viewer.grid_opacity_input,
                *page.viewer.layer_buttons.values()]
    if page.viewer.height_legend.isVisible():
        controls.append(page.viewer.height_legend.threshold)
    if not baseline:
        for caption, _ in page.console_fields:
            assert caption.contentsRect().width() >= caption.fontMetrics().horizontalAdvance(caption.text())
            controls.append(caption)
    rectangles = []
    for control in controls:
        assert control.isVisible(), control
        rect = QRect(control.mapTo(window, QPoint()), control.size())
        assert window.rect().contains(rect), (control, rect)
        assert all(not rect.intersects(other) for other in rectangles), (control, rect)
        rectangles.append(rect)


def save(window, filename):
    assert window.grab().save(str(output / filename))


app = QApplication([])
configure_application_font(app)
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='ccs-polish-render-') as tmp:
    root = Path(tmp)
    settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
    types = DeviceTypeTemplateRepository(root / 'types.json', root / 'icons')
    with patch('ccs_monitor.main_window.QSettings', return_value=settings), \
         patch('ccs_monitor.pages.map_page.QSettings', return_value=settings), \
         patch('ccs_monitor.pages.map_page.MAP_VIEWER_SETTINGS', MapViewerSettings(settings)), \
         patch('ccs_monitor.data_source.DeviceTypeTemplateRepository', return_value=types):
        source = SimulatedDeviceSource(DeviceConfigRepository(root / 'devices.json'))
        template = source.snapshots()[0]
        devices = []
        for device_id, name, kind, battery, asset in (
            ('QRD_002', '宇树Go2_2', 'quadruped', 83.0, 'qrd_6e3de5472a.svg'),
            ('UGV_004', '轮趣WheelTech R550P 02', 'ugv', 58.9, 'ugv_4c1f34e72b.svg'),
        ):
            icon = root / asset
            shutil.copyfile(repo / 'data/device_type_assets' / asset, icon)
            devices.append(replace(template, device_id=device_id, device_name=name, device_type=kind,
                                   battery_percent=battery, device_icon_path=str(icon),
                                   connection_status=ConnectionStatus.ONLINE, health_status=HealthStatus.NORMAL,
                                   flight_mode='unknown'))
        source.snapshots = lambda: devices
        source.device = lambda device_id: next((d for d in devices if d.device_id == device_id), None)
        maps = MapRepository(root / 'maps')
        definition = maps.create_empty('test_merge_go2_2_3')
        points = []
        for x in np.linspace(-6, 6, 151):
            for z in np.linspace(0, 2.8, 38):
                points.extend(((x, -4, z), (x, 4, z)))
        for y in np.linspace(-4, 4, 101):
            for z in np.linspace(0, 2.8, 38):
                points.extend(((-6, y, z), (6, y, z)))
        for x in np.linspace(-6, 6, 120):
            for y in np.linspace(-4, 4, 80):
                points.append((x, y, -0.05))
        path = root / 'source.pcd'
        write_ascii_pcd(path, points)
        definition = maps.import_pcd(definition.map_id, path)
        (root / 'map.pgm').write_text('P2\n120 80\n255\n' + ' '.join(
            '0' if x in (0, 119) or y in (0, 79) else '245'
            for y in range(80) for x in range(120)), encoding='ascii')
        (root / 'map.yaml').write_text(
            'image: map.pgm\nresolution: 0.1\norigin: [-6, -4, 0]\nnegate: 0\n'
            'occupied_thresh: 0.65\nfree_thresh: 0.2\n', encoding='ascii')
        definition = maps.import_pgm(definition.map_id, root / 'map.yaml')
        maps.set_active_map(definition.map_id)
        tasks = TaskRepository(root / 'tasks')
        task = tasks.create('联合任务01', definition, devices)
        for index, subtask in enumerate(task.subtasks):
            y = -2 if index == 0 else 2
            task = tasks.update_subtask(task.task_id, replace(subtask, waypoints=(
                TaskWaypoint('a', -4, y, 0), TaskWaypoint('b', 4, -y, 0))))
        window = MainWindow(source, simulated_overview(), map_repository=maps, task_repository=tasks)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        try:
            window.resize(1920, 1080)
            window.show()
            window.set_current_page(4)
            events(0.7)
            page = window.command_page
            page.device_panel.select_device('UGV_004')
            page._select_device('UGV_004')
            viewer = page.viewer
            viewer.fit_all()
            events(0.5)
            assert viewer._canvas.native.isValid(), 'Native OpenGL context is unavailable'
            if not baseline:
                assert len(viewer._task_paths) == 2
                assert viewer._task_conflicts
                assert all(v.visible for v in viewer._task_path_visuals[:2])
            now = time.monotonic()
            for index in range(60):
                page.trends.append('UGV_004', DeviceTelemetrySnapshot('UGV_004'), now - 59 + index,
                                   PoseTelemetry(x=index / 20, y=float(np.sin(index / 12)), z=0,
                                                 roll=float(np.sin(index / 8)), pitch=0, yaw=index / 10))
            records = []
            for theme in (ThemeMode.NIGHT, ThemeMode.DAY):
                window.apply_theme(theme, persist=False)
                window.resize(1920, 1080)
                events()
                page.upper_splitter.setSizes([240, 1290, 360])
                for width, height in ((1920, 1080), (1440, 900), (1024, 768), (800, 600)):
                    window.resize(width, height)
                    events()
                    viewer.fit_all()
                    events()
                    check_geometry(window, page)
                    assert (window.width(), window.height()) == (width, height)
                    save(window, f'{theme.value}-{width}x{height}.png')
                    records.append(dict(theme=theme.value, requested=[width, height],
                                        actual=[window.width(), window.height()],
                                        split=page.upper_splitter.sizes(), vertical=page.vertical_splitter.sizes(),
                                        canvas_valid=viewer._canvas.native.isValid(),
                                        task_routes=len(viewer._task_paths)))
            window.resize(1920, 1080)
            window.apply_theme(ThemeMode.NIGHT, persist=False)
            for mode in ('pointcloud', 'grid', 'overlay'):
                viewer.set_layer_mode(mode)
                viewer.fit_all()
                events()
                save(window, f'night-layer-{mode}.png')
                # The framebuffer is real GL output, not a mocked Qt widget.
                pixels = viewer._canvas.render()
                # A baseline binary PGM legitimately contains only a few colors.
                minimum_colors = 1 if mode == 'grid' else 30
                assert np.unique(pixels.reshape(-1, pixels.shape[-1]), axis=0).shape[0] > minimum_colors
                if not baseline:
                    paths = dict(viewer._task_paths)
                    conflicts = list(viewer._task_conflicts)
                    viewer.set_task_paths({})
                    viewer.set_task_conflicts([])
                    without_task = viewer._canvas.render()
                    viewer.set_task_paths(paths)
                    viewer.set_task_conflicts(conflicts)
                    changed_pixels = int(np.count_nonzero(np.any(pixels != without_task, axis=2)))
                    assert changed_pixels > 20, (mode, changed_pixels)
                    records.append(dict(layer=mode, overlay_changed_pixels=changed_pixels))
            page.device_panel.toggle_collapsed()
            page.status_panel.toggle_expanded()
            page.console_panel.toggle_collapsed()
            events()
            save(window, 'night-collapsed.png')
            page.device_panel.toggle_collapsed()
            page.status_panel.toggle_expanded()
            page.console_panel.toggle_collapsed()
            events()
            window.set_dashboard_fullscreen(True)
            events()
            page._viewer_escape_pressed()
            events()
            assert not window.dashboard_fullscreen
            assert viewer._canvas.native.isValid()
            (output / 'geometry.json').write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(dict(opengl=True, baseline=baseline, frames=len(records),
                                  fullscreen_escape_return=True, synthetic_points=len(points),
                                  task_routes=len(viewer._task_paths))), flush=True)
        finally:
            window.close()
            window.deleteLater()
            events()
