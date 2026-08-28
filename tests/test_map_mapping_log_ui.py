import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from ccs_monitor.map_building_v2 import RemoteMappingLogEntry, RemoteMappingSnapshot
from ccs_monitor.pages.map_page import MapDetailPage


class MappingLogUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_remote_log_is_compact_bounded_and_preserved_on_retry(self):
        page = MapDetailPage(viewer_factory=QWidget)
        now = datetime.now(timezone.utc)
        entries = tuple(
            RemoteMappingLogEntry(now, "TX", "prepare_mapping", "entry %d" % index)
            for index in range(205)
        )
        snapshot = RemoteMappingSnapshot(
            "map-1", "QRD_001", "session-1", "preparing", "正在协商", now,
            log_entries=entries,
        )
        page.update_remote_mapping(snapshot)
        self.assertFalse(page.mapping_log.isHidden())
        self.assertEqual(page.mapping_log.document().blockCount(), 200)
        self.assertIn("entry 204", page.mapping_log.toPlainText())
        retry_entries = entries[-199:] + (
            RemoteMappingLogEntry(now, "LOCAL", "recovery", "重新协商"),)
        page.update_remote_mapping(RemoteMappingSnapshot(
            "map-1", "QRD_001", "session-1", "preparing", "正在重新协商", now,
            log_entries=retry_entries,
        ))
        text = page.mapping_log.toPlainText()
        self.assertIn("entry 204", text)
        self.assertIn("重新协商", text)

    def test_device_filter_and_view_only_clear(self):
        page = MapDetailPage(viewer_factory=QWidget)
        now = datetime.now(timezone.utc)
        entries = (
            RemoteMappingLogEntry(now, "RX", "status", "Scout ready", "INFO", "UGV_001"),
            RemoteMappingLogEntry(now, "RX", "status", "WheelTech ready", "INFO", "UGV_003"),
        )
        snapshot = RemoteMappingSnapshot(
            "map-1", "UGV_001", "job-1", "ready", "ready", now,
            log_entries=entries,
        )
        page.update_remote_mapping(snapshot)
        self.assertIn("UGV_001", page.mapping_log.toPlainText())
        self.assertIn("UGV_003", page.mapping_log.toPlainText())
        page.mapping_device_filter.setCurrentIndex(
            page.mapping_device_filter.findData("UGV_003")
        )
        self.assertNotIn("Scout ready", page.mapping_log.toPlainText())
        self.assertIn("WheelTech ready", page.mapping_log.toPlainText())
        page._clear_mapping_log_view()
        self.assertEqual(page.mapping_log.toPlainText(), "")
        later = datetime.now(timezone.utc) + timedelta(seconds=1)
        page.update_remote_mapping(RemoteMappingSnapshot(
            "map-1", "UGV_001", "job-1", "mapping", "mapping", now,
            log_entries=entries + (
                RemoteMappingLogEntry(
                    later, "RX", "fragment", "new fragment", "INFO", "UGV_003"
                ),
            ),
        ))
        self.assertIn("new fragment", page.mapping_log.toPlainText())
        self.assertIsNot(page.mapping_log.parentWidget(), page.relocalization_log.parentWidget())


if __name__ == "__main__":
    unittest.main()
