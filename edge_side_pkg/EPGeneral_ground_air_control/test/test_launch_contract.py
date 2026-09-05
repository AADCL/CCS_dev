import os
import unittest
from xml.etree import ElementTree


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LaunchContractTests(unittest.TestCase):
    def test_control_launch_contains_only_relocalization_stack(self):
        path = os.path.join(ROOT, "launch", "relocalization_control.launch")
        launch = ElementTree.parse(path).getroot()
        includes = [item.attrib["file"] for item in launch.findall("include")]
        self.assertEqual(includes, [
            "$(find fast_lio_open3d)/launch/mapping_mid360.launch",
            "$(find car_bringup)/launch/fastlio_pipeline.launch",
            "$(find ground_air_localization)/launch/localization.launch",
        ])
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
        self.assertNotIn("mapping_coordinate_transforms.launch", text)
        self.assertNotIn("livox_mid360_base.launch", text)
        node = launch.find("node")
        self.assertEqual(node.attrib["name"], "ground_air_initial_pose_adapter")


if __name__ == "__main__":
    unittest.main()
