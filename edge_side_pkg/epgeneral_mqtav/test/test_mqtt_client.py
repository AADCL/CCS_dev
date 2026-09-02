import json
import unittest
from pathlib import Path

from epgeneral_mqtav.config import load_config
from epgeneral_mqtav.mqtt_client import MqttPublisher
from epgeneral_mqtav.state import HealthState


ROOT = Path(__file__).resolve().parents[1]
SHARED_CONFIG = ROOT.parent / "EPGeneral_device_config" / "config"
MQTAV_CONFIG = SHARED_CONFIG / "epgeneral_mqtav.yaml"
DEVICE_CONFIG = SHARED_CONFIG / "device.yaml"


class Result(object):
    rc = 0


class FakeClient(object):
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.published = []
        self.will = None
        self.started = False
        self.disconnected = False
        self.on_connect = None
        self.on_disconnect = None
        self.on_connect_fail = None

    def reconnect_delay_set(self, **kwargs):
        self.reconnect = kwargs

    def will_set(self, topic, payload, qos, retain):
        self.will = (topic, payload, qos, retain)

    def connect_async(self, host, port, keepalive):
        self.connection = (host, port, keepalive)

    def loop_start(self):
        self.started = True

    def loop_stop(self):
        self.started = False

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, payload, qos, retain))
        return Result()

    def disconnect(self):
        self.disconnected = True


class FakeLogger(object):
    def __init__(self):
        self.events = []

    def info(self, message, *args):
        self.events.append(("info", message % args if args else message))

    def warning(self, message, *args):
        self.events.append(("warning", message % args if args else message))

    def error(self, message, *args):
        self.events.append(("error", message % args if args else message))


class MqttTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(MQTAV_CONFIG, DEVICE_CONFIG)
        self.health = HealthState(self.config.device)
        self.logger = FakeLogger()
        self.created = []

        def factory(**kwargs):
            client = FakeClient(**kwargs)
            self.created.append(client)
            return client

        self.publisher = MqttPublisher(self.config, self.health, self.logger, factory)
        self.client = self.created[0]

    def test_sets_client_identity_and_offline_last_will(self):
        self.assertEqual(self.client.kwargs["client_id"], "mqtav-UAV_001")
        self.assertEqual(self.client.reconnect, {"min_delay": 1, "max_delay": 60})
        topic, payload, qos, retain = self.client.will or ("", "", 0, False)
        self.assertEqual(topic, "mqtav/UAV_001/presence")
        self.assertEqual(json.loads(payload)["status"], "offline")
        self.assertTrue(json.loads(payload)["session_id"])
        self.assertEqual(qos, 1)
        self.assertTrue(retain)

    def test_connect_publishes_presence_and_fresh_snapshots(self):
        self.publisher.start()
        self.assertEqual(self.client.connection, ("192.168.20.10", 1883, 10))
        self.publisher._on_connect(self.client, None, None, 0)
        self.assertTrue(self.publisher.connected)
        self.assertEqual([item[0] for item in self.client.published], [
            "mqtav/UAV_001/presence",
            "mqtav/UAV_001/heartbeat",
            "mqtav/UAV_001/status",
        ])
        self.assertEqual(json.loads(self.client.published[-1][1])["message_type"], "status")
        sessions = {json.loads(item[1])["session_id"] for item in self.client.published}
        self.assertEqual(len(sessions), 1)
        self.assertTrue(any("heartbeat_sent" in message for _level, message in self.logger.events))

    def test_disconnected_publisher_drops_stale_status(self):
        self.assertFalse(self.publisher.publish_status())
        self.assertEqual(self.client.published, [])

    def test_normal_stop_publishes_offline_presence(self):
        self.publisher._on_connect(self.client, None, None, 0)
        self.publisher.stop()
        self.assertEqual(json.loads(self.client.published[-1][1])["status"], "offline")
        self.assertTrue(self.client.disconnected)
