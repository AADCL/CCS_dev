import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from epgeneral_mqtav.config import MissionConfig, RosTopicConfig, load_config
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
        self.timers = []

    def Subscriber(self, topic, message_type, callback, queue_size):
        record = (topic, message_type, callback, queue_size)
        self.subscriptions.append(record)
        return record

    def Duration(self, seconds):
        return seconds

    def Timer(self, duration, callback):
        timer = SimpleNamespace(duration=duration, callback=callback)
        self.timers.append(timer)
        return timer


class RosBridgeTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(MQTAV_CONFIG, DEVICE_CONFIG)
        self.health = HealthState(self.config.device)
        self.rospy = FakeRospy()
        self.logger = FakeLogger()
        self.bridge = RosBridge(self.config, self.health, self.logger, self.rospy, lambda _name: object)
        clock_patch = patch("epgeneral_mqtav.ros_bridge.time.monotonic", return_value=100.0)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def configure_latched_state(self, independent_connection=True):
        self.config.ros.state = RosTopicConfig(
            "/go2/control/enabled", "std_msgs/Bool",
            {"connected": None, "armed": "data", "system_status": None, "mode": None},
            connected_on_message=True, timeout_seconds=3.0,
        )
        if independent_connection:
            self.config.ros.connection = RosTopicConfig(
                "/robot/heartbeat", "std_msgs/Empty", timeout_seconds=3.0,
            )
        self.bridge.start()

    def test_connection_without_initial_data_remains_disconnected(self):
        self.configure_latched_state()
        self.assertEqual(
            [item[0] for item in self.rospy.subscriptions],
            ["/robot/heartbeat", "/go2/control/enabled", "/mavros/battery"],
        )
        self.assertEqual(len(self.rospy.timers), 1)
        self.assertEqual(self.rospy.timers[0].duration, 1.0)
        self.assertFalse(self.health.payload("status")["health"]["fcu_connected"])
        self.bridge._on_state(SimpleNamespace(data=False))
        self.rospy.timers[0].callback(None)
        health = self.health.payload("status")["health"]
        self.assertFalse(health["fcu_connected"])
        self.assertFalse(health["armed"])

    def test_periodic_connection_keeps_latched_state_online(self):
        self.configure_latched_state()
        self.bridge._on_state(SimpleNamespace(data=True))
        for now in (101.0, 103.0, 105.0):
            self.clock.return_value = now
            self.bridge._on_connection(object())
            self.rospy.timers[0].callback(None)
            health = self.health.payload("status")["health"]
            self.assertTrue(health["fcu_connected"])
            self.assertTrue(health["armed"])

    def test_connection_timeout_and_recovery_preserve_armed_state(self):
        self.configure_latched_state()
        self.bridge._on_state(SimpleNamespace(data=False))
        self.bridge._on_connection(object())
        self.clock.return_value = 103.0
        self.rospy.timers[0].callback(None)
        self.assertTrue(self.health.payload("status")["health"]["fcu_connected"])
        self.clock.return_value = 103.01
        self.rospy.timers[0].callback(None)
        self.assertFalse(self.health.payload("status")["health"]["fcu_connected"])
        self.bridge._on_state(SimpleNamespace(data=True))
        self.assertFalse(self.health.payload("status")["health"]["fcu_connected"])
        self.clock.return_value = 104.0
        self.bridge._on_connection(object())
        health = self.health.payload("status")["health"]
        self.assertTrue(health["fcu_connected"])
        self.assertTrue(health["armed"])

    def test_mapped_state_cannot_override_independent_connection(self):
        self.config.ros.connection = RosTopicConfig(
            "/robot/heartbeat", "std_msgs/Empty", timeout_seconds=3.0,
        )
        self.bridge.start()
        self.bridge._on_connection(object())
        self.bridge._on_state(SimpleNamespace(connected=False, armed=False, system_status=3, mode="IDLE"))
        health = self.health.payload("status")["health"]
        self.assertTrue(health["fcu_connected"])
        self.assertEqual(health["system_status"], 3)
        self.assertEqual(health["flight_mode"], "IDLE")

    def test_legacy_state_freshness_still_expires_and_recovers(self):
        self.configure_latched_state(independent_connection=False)
        self.assertIsNone(self.health.payload("status")["health"]["fcu_connected"])
        self.rospy.timers[0].callback(None)
        self.assertFalse(self.health.payload("status")["health"]["fcu_connected"])
        self.bridge._on_state(SimpleNamespace(data=False))
        self.assertTrue(self.health.payload("status")["health"]["fcu_connected"])
        self.clock.return_value = 104.0
        self.rospy.timers[0].callback(None)
        self.assertFalse(self.health.payload("status")["health"]["fcu_connected"])
        self.bridge._on_state(SimpleNamespace(data=True))
        self.assertTrue(self.health.payload("status")["health"]["fcu_connected"])

    def test_subscribes_to_default_mavros_topics_and_maps_messages(self):
        self.bridge.start()
        self.assertEqual([item[0] for item in self.rospy.subscriptions], ["/mavros/state", "/mavros/battery"])
        self.assertEqual(self.rospy.timers, [])
        self.bridge._on_state(SimpleNamespace(connected=True, armed=True, system_status=4, mode="GUIDED"))
        self.bridge._on_battery(SimpleNamespace(percentage=0.5, voltage=16.2, current=3.1))
        health = self.health.payload("status")["health"]
        self.assertEqual(health["flight_mode"], "GUIDED")
        self.assertEqual(health["battery"]["percentage"], 50.0)
        self.assertTrue(health["fcu_connected"])
        self.bridge._on_state(SimpleNamespace(connected=False, armed=False, system_status=3, mode="IDLE"))
        self.assertFalse(self.health.payload("status")["health"]["fcu_connected"])

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
