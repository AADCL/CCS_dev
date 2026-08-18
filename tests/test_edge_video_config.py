import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "edge_side_pkg" / "EPGeneral_usb_cam_rtsp"


class EdgeVideoConfigTests(unittest.TestCase):
    def test_default_rtsp_contract_matches_ground_station(self):
        config = yaml.safe_load((PACKAGE / "config" / "video.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["rtsp_port"], 8554)
        self.assertEqual(config["rtsp_mount_point"], "/usb_cam")
        self.assertEqual(config["image_topic"], "/camera/image_raw")
        self.assertEqual(config["image_message_type"], "sensor_msgs/Image")
        self.assertGreater(config["output_width"], 0)
        self.assertGreater(config["output_height"], 0)
        self.assertGreater(config["framerate"], 0)
        self.assertGreater(config["bitrate_kbps"], 0)

    def test_launch_uses_shared_device_config_and_only_rtsp_node(self):
        launch = (PACKAGE / "launch" / "epgeneral_usb_cam_rtsp.launch").read_text(encoding="utf-8")
        self.assertIn("$(find epgeneral_device_config)/config/device.yaml", launch)
        self.assertNotIn('pkg="usb_cam"', launch)
        self.assertIn('pkg="epgeneral_usb_cam_rtsp"', launch)

    def test_realsense_profile_consumes_existing_color_topic(self):
        config = yaml.safe_load((PACKAGE / "config" / "realsense_d435i.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["camera_model"], "Intel RealSense D435i")
        self.assertEqual(config["image_topic"], "/camera/color/image_raw")
        self.assertEqual(config["image_message_type"], "sensor_msgs/Image")
        launch = (PACKAGE / "launch" / "epgeneral_realsense_d435i_rtsp.launch").read_text(encoding="utf-8")
        self.assertNotIn('pkg="usb_cam"', launch)
        self.assertIn('pkg="epgeneral_usb_cam_rtsp"', launch)

    def test_compressed_profile_and_source_support_compressed_images(self):
        config = yaml.safe_load((PACKAGE / "config" / "compressed_video.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["image_message_type"], "sensor_msgs/CompressedImage")
        self.assertGreater(config["output_width"], 0)
        self.assertGreater(config["output_height"], 0)
        source = (PACKAGE / "src" / "epgeneral_usb_cam_rtsp_node.cpp").read_text(encoding="utf-8")
        self.assertIn("compressedImageCallback", source)
        self.assertIn("cv::imdecode", source)


if __name__ == "__main__":
    unittest.main()
