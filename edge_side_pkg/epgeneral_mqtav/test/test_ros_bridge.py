import unittest
from pathlib import Path
from types import SimpleNamespace

from epgeneral_mqtav.config import MissionConfig, load_config
from epgeneral_mqtav.ros_bridge import RosBridge, read_field
from epgeneral_mqtav.state import HealthState


ROOT = Path(__file__).resolve().parents[1]
SHARED_CONFIG = ROOT.parent / "EPGeneral_device_config" / "config"
MQTAV_CONFIG = SHARED_CONFIG / "epgeneral_mqtav.yaml"
DEVICE_CONFIG = SHARED_CONFIG / "device.yaml"


class FakeLogger(object):
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(message % args)

    def warning(self, message, *args):
        self.messages.append(message % args)


class FakeRospy(object):
    def __init__(self):
        self.subscriptions = []

    def Subscriber(self, topic, message_type, callback, queue_size):
        record = (topic, message_type, callback, queue_size)
        self.subscriptions.append(record)
        return record


class RosBridgeTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(MQTAV_CONFIG, DEVICE_CONFIG)
        self.health = HealthState(self.config.device)
        self.rospy = FakeRospy()
        self.logger = FakeLogger()
        self.bridge = RosBridge(self.config, self.health, self.logger, self.rospy, lambda _name: object)

    def test_subscribes_to_default_mavros_topics_and_maps_messages(self):
        self.bridge.start()
        self.assertEqual([item[0] for item in self.rospy.subscriptions], ["/mavros/state", "/mavros/battery"])
        self.bridge._on_state(SimpleNamespace(connected=True, armed=True, system_status=4, mode="GUIDED"))
        self.bridge._on_battery(SimpleNamespace(percentage=0.5, voltage=16.2, current=3.1))
        health = self.health.payload("status")["health"]
        self.assertEqual(health["flight_mode"], "GUIDED")
        self.assertEqual(health["battery"]["percentage"], 50.0)

    def test_optional_mission_field_path(self):
        self.config.ros.mission = MissionConfig(True, "/mission/status", "std_msgs/String", "data.phase")
        bridge = RosBridge(self.config, self.health, self.logger, self.rospy, lambda _name: object)
        bridge.start()
        bridge._on_mission(SimpleNamespace(data=SimpleNamespace(phase="executing")))
        self.assertEqual(self.health.payload("status")["health"]["mission_status"], "executing")

    def test_disabled_battery_does_not_create_subscription(self):
        self.config.ros.battery.enabled = False
        self.bridge.start()
        self.assertEqual(
            [item[0] for item in self.rospy.subscriptions],
            ["/mavros/state"],
        )
        self.assertIn(
            "ros_subscription_disabled stream=battery",
            self.logger.messages,
        )

    def test_read_field_rejects_empty_path_components(self):
        with self.assertRaises(ValueError):
            read_field(SimpleNamespace(data="ok"), "data..status")
