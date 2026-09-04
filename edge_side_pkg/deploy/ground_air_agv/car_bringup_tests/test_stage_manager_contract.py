#!/usr/bin/env python3
from pathlib import Path
import unittest
from xml.etree import ElementTree


HERE = Path(__file__).resolve()
PROFILE = HERE.parents[1]
if (PROFILE / "car_bringup_scripts").is_dir():
    SCRIPTS = PROFILE / "car_bringup_scripts"
    LAUNCH = PROFILE / "launch"
else:
    SCRIPTS = PROFILE / "scripts"
    LAUNCH = PROFILE / "launch"


def include_files(root):
    return [item.attrib["file"] for item in root.findall("include")]


def node_params(node):
    return {item.attrib["name"]: item.attrib["value"] for item in node.findall("param")}


class StageManagerContractTests(unittest.TestCase):
    def test_manager_exposes_stage_interfaces_without_owning_static_tf(self):
        node = (SCRIPTS / "ground_air_stage_manager_node.py").read_text(
            encoding="utf-8"
        )
        for required in (
            "/ground_air/system/set_stage",
            "/ground_air/system/stage",
            "/ground_air/system/stage_detail",
            'rospy.set_param("~external_tf_required", 1)',
            "if stage == MAPPING:",
            '("map", "odom")',
            '("odom", "camera_init")',
            '("camera_init", "body")',
            '("body", "base_link")',
        ):
            self.assertIn(required, node)
        for forbidden in (
            "mapping_coordinate_transforms.launch",
            "_TRANSFORM_COMMAND",
            "_resident_transforms",
            "/cmd_vel",
            "/mavros/cmd",
            "/mavros/set_mode",
        ):
            self.assertNotIn(forbidden, node)

    def test_transform_launch_contains_only_the_static_pair(self):
        root = ElementTree.parse(
            LAUNCH / "mapping_coordinate_transforms.launch"
        ).getroot()
        self.assertEqual(root.findall("include"), [])
        nodes = {item.attrib["name"]: item.attrib for item in root.findall("node")}
        self.assertEqual(
            set(nodes),
            {"odom_camera_init_broadcaster", "base_link_body_broadcaster"},
        )
        self.assertTrue(all(item["pkg"] == "tf2_ros" for item in nodes.values()))

    def test_mapping_entrypoint_owns_only_on_demand_mapping_processes(self):
        path = LAUNCH / "manual_mapping_control.launch"
        root = ElementTree.parse(path).getroot()
        self.assertEqual(
            include_files(root),
            [
                "$(find fast_lio_open3d)/launch/mapping_mid360.launch",
                "$(find car_bringup)/launch/fastlio_pipeline.launch",
                "$(find pcl_test)/launch/filter_ground.launch",
                "$(find dynamic_mapping)/launch/dynamic_mapping.launch",
                "$(find ground_air_mapping)/launch/mapping.launch",
            ],
        )
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "start_mapping.launch",
            "mapping_system.launch",
            "mapping_coordinate_transforms.launch",
        ):
            self.assertNotIn(forbidden, text)

        nodes = {item.attrib["name"]: item for item in root.findall("node")}
        self.assertEqual(
            set(nodes),
            {"ground_air_world_tf_owner", "ground_air_start_mapping"},
        )
        self.assertEqual(
            node_params(nodes["ground_air_world_tf_owner"]),
            {
                "mode": "mapping",
                "map_frame": "map",
                "odom_frame": "odom",
                "rate": "20.0",
            },
        )
        self.assertEqual(
            node_params(nodes["ground_air_start_mapping"]),
            {
                "operation": "start_mapping",
                "map_id": "$(arg map_id)",
                "service_wait_timeout": "$(arg service_wait_timeout)",
                "keep_alive": "true",
            },
        )

    def test_relocalization_entrypoint_reuses_external_static_tf(self):
        path = LAUNCH / "relocalization_control.launch"
        root = ElementTree.parse(path).getroot()
        self.assertEqual(
            include_files(root),
            [
                "$(find fast_lio_open3d)/launch/mapping_mid360.launch",
                "$(find car_bringup)/launch/fastlio_pipeline.launch",
                "$(find ground_air_localization)/launch/localization.launch",
            ],
        )
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "start_relocalization.launch",
            "relocalization_system.launch",
            "mapping_coordinate_transforms.launch",
        ):
            self.assertNotIn(forbidden, text)
        nodes = root.findall("node")
        self.assertEqual([item.attrib["name"] for item in nodes],
                         ["ground_air_start_relocalization"])
        self.assertEqual(node_params(nodes[0])["operation"], "relocalize")


if __name__ == "__main__":
    unittest.main()
