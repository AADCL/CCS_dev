import importlib.util
import os
import unittest


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None

if PYSIDE_AVAILABLE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QAbstractSpinBox, QApplication

    from ccs_monitor.models import MapBounds, MapDefinition
    from ccs_monitor.pages.map_page import PgmGenerationDialog
    from ccs_monitor.widgets import NoButtonDoubleSpinBox, NoButtonSpinBox


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class InputWidgetV012Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_shared_numeric_inputs_hide_increment_buttons(self):
        self.assertEqual(
            NoButtonSpinBox().buttonSymbols(),
            QAbstractSpinBox.ButtonSymbols.NoButtons,
        )
        self.assertEqual(
            NoButtonDoubleSpinBox().buttonSymbols(),
            QAbstractSpinBox.ButtonSymbols.NoButtons,
        )

    def test_pgm_dialog_defaults_and_validation(self):
        definition = MapDefinition(
            "map-1", "测试地图", bounds=MapBounds(0, 0, -1, 2, 3, 2),
            pcd_path="map.pcd",
        )
        dialog = PgmGenerationDialog(definition)
        self.assertAlmostEqual(dialog.resolution.value(), 0.05)
        self.assertAlmostEqual(dialog.min_z.value(), -0.85)
        self.assertEqual(dialog.empty_cell.currentData(), "unknown")
        for control in (
            dialog.resolution, dialog.min_z, dialog.max_z, dialog.padding,
            dialog.min_points, dialog.inflation, dialog.free_threshold,
            dialog.occupied_threshold,
        ):
            self.assertEqual(
                control.buttonSymbols(), QAbstractSpinBox.ButtonSymbols.NoButtons
            )
        dialog.min_z.setValue(10)
        self.assertFalse(dialog.generate_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
