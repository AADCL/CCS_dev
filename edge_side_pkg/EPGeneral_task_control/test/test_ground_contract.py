import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from xml.etree import ElementTree

from ccs_monitor.task_config import load_task_system_config
from ccs_monitor.task_models import DeviceSubtask, TaskDefinition, TaskWaypoint
from ccs_monitor.task_protocol import TaskProtocol

from epgeneral_task_control.config import load_config
from epgeneral_task_control.storage import TrajectoryStore, decode_trajectory


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_CONFIG = os.path.join(
    os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "task_control.yaml")
DEVICE_CONFIG = os.path.join(PACKAGE, "test", "fixtures", "device.yaml")


class GroundContractTests(unittest.TestCase):
    def test_ground_encoder_persists_as_edge_xml(self):
        config = load_config(TASK_CONFIG, DEVICE_CONFIG)
        now = datetime(2026, 8, 13, tzinfo=timezone.utc)
        subtask = DeviceSubtask(
            "sub-ground", config["device_id"], "设备", "UAV", config["device_ip"],
            waypoints=(TaskWaypoint("wp-a", 1.0, 2.0, 3.0), TaskWaypoint("wp-b", 4.0, 5.0, 6.0)),
            cruise_speed_mps=1.5, start_delay_seconds=2.0, revision=4,
        )
        task = TaskDefinition("task-ground", "园区巡检", "map-ground", "地图", "map", "fingerprint",
                              created_at=now, updated_at=now, subtasks=(subtask,))
        encoded = TaskProtocol(load_task_system_config()).encode_subtask(task, subtask)
        payload = decode_trajectory(encoded.compressed, encoded.crc32, config, {
            "task_id": task.task_id, "subtask_id": subtask.subtask_id,
            "device_id": subtask.device_id, "revision": subtask.revision,
        }, encoded.raw_bytes)
        with tempfile.TemporaryDirectory() as directory:
            saved = TrajectoryStore(directory).commit(payload, encoded.crc32)
            root = ElementTree.parse(saved["xml_path"]).getroot()
            self.assertEqual(root.get("revision"), "4")
            self.assertEqual(root.find("metadata").get("task_name"), "园区巡检")
            self.assertEqual(root.find("waypoints").get("count"), "2")


if __name__ == "__main__":
    unittest.main()
