import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "edge_side_pkg" / "EPGeneral_video_srt"
SHARED_CONFIG = (
    ROOT / "edge_side_pkg" / "EPGeneral_device_config" / "config")


class EdgeVideoConfigTests(unittest.TestCase):
    def test_package_identity_and_patch_version(self):
        manifest = ET.parse(PACKAGE / "package.xml").getroot()
        self.assertEqual(manifest.findtext("name"), "epgeneral_video_srt")
        self.assertEqual(manifest.findtext("version"), "0.1.1")

    def test_default_srt_listener_contract(self):
        config = yaml.safe_load(
            (SHARED_CONFIG / "video.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["srt_bind_address"], "0.0.0.0")
        self.assertEqual(config["srt_port"], 9000)
        self.assertEqual(config["srt_latency_ms"], 120)
        self.assertEqual(config["image_message_type"], "sensor_msgs/Image")

    def test_launch_uses_shared_configuration(self):
        launch = (
            PACKAGE / "launch" / "epgeneral_video_srt.launch"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "$(find epgeneral_device_config)/config/device.yaml", launch)
        self.assertIn(
            "$(find epgeneral_device_config)/config/video.yaml", launch)
        self.assertIn('pkg="epgeneral_video_srt"', launch)
        self.assertFalse((PACKAGE / "config").exists())

    def test_raw_and_compressed_subscriptions_remain_supported(self):
        source = (
            PACKAGE / "src" / "epgeneral_video_srt_node.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("compressedImageCallback", source)
        self.assertIn("cv_bridge::toCvCopy", source)

    def test_pipeline_is_low_latency_h264_mpegts_srt_listener(self):
        source = (
            PACKAGE / "src" / "epgeneral_video_srt_node.cpp"
        ).read_text(encoding="utf-8")
        for required in (
            "byte-stream=true",
            "profile=baseline",
            "bframes=0",
            "aud=true",
            "h264parse config-interval=-1",
            "mpegtsmux alignment=7",
            "srtsink",
            "mode=listener",
        ):
            self.assertIn(required, source)
        self.assertNotIn("gstreamer-rtsp-server", source.lower())


if __name__ == "__main__":
    unittest.main()
