import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "edge_side_pkg" / "EPGeneral_video_srt"


class EdgeVideoConfigTests(unittest.TestCase):
    def test_package_is_renamed_and_starts_at_version_010(self):
        manifest = ET.parse(PACKAGE / "package.xml").getroot()
        self.assertEqual(manifest.findtext("name"), "epgeneral_video_srt")
        self.assertEqual(manifest.findtext("version"), "0.1.0")

    def test_default_srt_listener_contract(self):
        config = yaml.safe_load((PACKAGE / "config" / "video.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["srt_bind_address"], "0.0.0.0")
        self.assertEqual(config["srt_port"], 9000)
        self.assertEqual(config["srt_latency_ms"], 120)
        self.assertEqual(config["image_topic"], "/camera/image_raw")
        self.assertEqual(config["image_message_type"], "sensor_msgs/Image")

    def test_launch_uses_shared_device_config_and_srt_node(self):
        launch = (PACKAGE / "launch" / "epgeneral_video_srt.launch").read_text(encoding="utf-8")
        self.assertIn("$(find epgeneral_device_config)/config/device.yaml", launch)
        self.assertNotIn('pkg="usb_cam"', launch)
        self.assertIn('pkg="epgeneral_video_srt"', launch)
        self.assertIn('type="epgeneral_video_srt_node"', launch)

    def test_realsense_and_compressed_profiles_keep_topic_subscriptions(self):
        realsense = yaml.safe_load((PACKAGE / "config" / "realsense_d435i.yaml").read_text(encoding="utf-8"))
        compressed = yaml.safe_load((PACKAGE / "config" / "compressed_video.yaml").read_text(encoding="utf-8"))
        self.assertEqual(realsense["image_topic"], "/camera/color/image_raw")
        self.assertEqual(compressed["image_message_type"], "sensor_msgs/CompressedImage")
        source = (PACKAGE / "src" / "epgeneral_video_srt_node.cpp").read_text(encoding="utf-8")
        self.assertIn("compressedImageCallback", source)
        self.assertIn("cv_bridge::toCvCopy", source)

    def test_pipeline_is_low_latency_h264_mpegts_srt_listener(self):
        source = (PACKAGE / "src" / "epgeneral_video_srt_node.cpp").read_text(encoding="utf-8")
        for required in (
            "byte-stream=true", "profile=baseline", "bframes=0", "aud=true",
            "h264parse config-interval=-1", "mpegtsmux alignment=7", "srtsink",
            "mode=listener", "wait-for-connection=false",
            "caller-connecting", "caller-added", "caller-removed",
        ):
            self.assertIn(required, source)
        operational = "\n".join(
            (PACKAGE / name).read_text(encoding="utf-8")
            for name in ("CMakeLists.txt", "package.xml", "config/video.yaml")
        ).lower()
        self.assertNotIn("rtsp", operational)
        self.assertNotIn("8554", operational)
        self.assertNotIn("mount_point", operational)
        self.assertNotIn("gstreamer-rtsp-server", operational)


if __name__ == "__main__":
    unittest.main()
