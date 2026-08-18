import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ccs_monitor.models import (
    DeviceSnapshot, MapBounds, MapDefinition, MapFusionAlgorithm, MapStatus, PgmMapMetadata,
)
from ccs_monitor.pages.map_page import MapFusionDialog, PgmFusionDialog
from ccs_monitor.widgets import NoButtonDoubleSpinBox


class PgmFusionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_source_validation_ip_guard_and_no_button_inputs(self):
        pgm = PgmMapMetadata(
            "map.pgm", "map.yaml", 0.05, 0, 0, 0, 10, 10, False, 0.65, 0.196,
        )
        target = MapDefinition(
            "map-1", "Target", status=MapStatus.READY, pcd_path="map.pcd",
            bounds=MapBounds(0, 0, 0, 10, 10, 2), pgm=pgm,
        )
        devices = [
            DeviceSnapshot("D1", "Online", "UGV", ip_address="127.0.0.1"),
            DeviceSnapshot("D2", "No IP", "UAV"),
        ]
        dialog = PgmFusionDialog([target], devices)
        available = dialog._controls["D1"]
        unavailable = dialog._controls["D2"]
        self.assertTrue(available[0].isEnabled())
        self.assertFalse(unavailable[0].isEnabled())
        self.assertIsInstance(available[2], NoButtonDoubleSpinBox)
        self.assertFalse(dialog.start_button.isEnabled())
        available[0].setChecked(True)
        available[1].setText("edge-map")
        dialog.include_existing.setChecked(True)
        self.assertTrue(dialog.start_button.isEnabled())
        sources = dialog.selected_remote_sources()
        self.assertEqual(sources[0].source_map_id, "edge-map")
        self.assertEqual(sources[0].device_ip, "127.0.0.1")
        dialog.close()

    def test_offline_fusion_sync_pgm_requires_every_selected_map_layer(self):
        pgm = PgmMapMetadata(
            "map.pgm", "map.yaml", 0.05, 0, 0, 0, 10, 10, False, 0.65, 0.196,
        )
        maps = [
            MapDefinition("a", "A", status=MapStatus.READY, pcd_path="map.pcd", pgm=pgm),
            MapDefinition("b", "B", status=MapStatus.READY, pcd_path="map.pcd"),
        ]
        algorithm = MapFusionAlgorithm("builtin", "Builtin", "1", "", "0" * 64, is_default=True)
        dialog = MapFusionDialog(maps, [algorithm])
        dialog.name_input.setText("Merged")
        for row in range(dialog.map_list.count()):
            dialog.map_list.item(row).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dialog.fuse_button.isEnabled())
        dialog.sync_pgm.setChecked(True)
        self.assertFalse(dialog.fuse_button.isEnabled())
        self.assertIn("所有选中的地图", dialog.validation.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
