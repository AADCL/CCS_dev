import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "edge_side_pkg" / "usb_cam_rtsp"


class EdgeVideoConfigTests(unittest.TestCase):
    def test_default_rtsp_contract_matches_ground_station(self):
        config = yaml.safe_load((PACKAGE / "config" / "video.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["rtsp_port"], 8554)
        self.assertEqual(config["rtsp_mount_point"], "/usb_cam")
        self.assertEqual(config["image_topic"], "/usb_cam/image_raw")
        self.assertGreater(config["image_width"], 0)
        self.assertGreater(config["image_height"], 0)
        self.assertGreater(config["framerate"], 0)
        self.assertGreater(config["bitrate_kbps"], 0)

    def test_launch_uses_shared_device_config_and_usb_cam(self):
        launch = (PACKAGE / "launch" / "usb_cam_rtsp.launch").read_text(encoding="utf-8")
        self.assertIn("$(find edge_device_config)/config/device.yaml", launch)
        self.assertIn('pkg="usb_cam"', launch)
        self.assertIn('pkg="usb_cam_rtsp"', launch)


if __name__ == "__main__":
    unittest.main()
