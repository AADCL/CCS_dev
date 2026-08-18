import os
import unittest
from types import SimpleNamespace

import msgpack

from epgeneral_multi_map.config import load_config
from epgeneral_multi_map.node import RosMultiMapNode
from epgeneral_multi_map.protocol import encode_envelope


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(PACKAGE, "config", "multi_mapping.yaml")
DEVICE = os.path.join(
    os.path.dirname(PACKAGE), "EPGeneral_device_config", "config", "device.yaml"
)
GROUND_IP = "192.168.151.100"


class FakeClock:
    def __init__(self, wall_ns=1_000_000_000):
        self.wall_ns = wall_ns
        self.monotonic_value = 10.0

    def time_ns(self):
        return self.wall_ns

    def monotonic(self):
        return self.monotonic_value


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, datagram, destination):
        self.sent.append((datagram, destination))


class FakeSubscriber:
    def __init__(self, topic, message_class, callback, **kwargs):
        self.topic = topic
        self.message_class = message_class
        self.callback = callback
        self.kwargs = kwargs
        self.unregistered = False

    def unregister(self):
        self.unregistered = True


class FakeRospy:
    def __init__(self, config, topics=None):
        self.config = config
        self.topics = topics or [
            (config["cloud_topic"], config["cloud_message_type"]),
            (config["pose_topic"], config["pose_message_type"]),
        ]

    def get_published_topics(self):
        return list(self.topics)

    def Subscriber(self, *args, **kwargs):
        return FakeSubscriber(*args, **kwargs)

    def __getattr__(self, name):
        if name.startswith("log"):
            return lambda *unused_args, **unused_kwargs: None
        raise AttributeError(name)


def make_node(wall_ns=1_000_000_000, topics=None):
    config = load_config(CONFIG, DEVICE)
    clock = FakeClock(wall_ns)
    rospy = FakeRospy(config, topics)
    node = RosMultiMapNode(
        rospy, config, clock=clock, message_resolver=lambda unused: object,
        point_reader=lambda message, **unused: iter(message.points),
    )
    node.socket = FakeSocket()
    return node, clock


def start_datagram(node, request_id="request-1", session_id="session-1",
                   start_at_ns=2_000_000_000, sent_at_ns=None, joint=True):
    payload = {
        "request_id": request_id,
        "return_host": GROUND_IP,
        "return_port": node.config["data_port"],
        "cloud_rate_hz": 5.0,
        "voxel_size_m": 0.1,
        "compression": "zlib",
        "point_format": "xyz_f32_le",
        "coordinate_contract": "sensor+map_body+body_sensor",
    }
    if joint:
        payload.update({
            "job_id": "job-1",
            "role": "primary",
            "primary_device_id": node.config["device_id"],
            "participant_device_ids": [node.config["device_id"], "UGV_002"],
            "start_at_ns": start_at_ns,
            "slice_duration_ns": 5_000_000_000,
        })
    identity = {"map_id": "map-1", "session_id": session_id}
    return encode_envelope(
        node.config, identity, "start_mapping", 0, payload,
        sent_at_ns=node.clock.time_ns() if sent_at_ns is None else sent_at_ns,
    )


def stop_datagram(node, stop_at_ns, request_id="stop-1", session_id="session-1",
                  job_id="job-1"):
    payload = {"request_id": request_id, "reason": "operator stop",
               "job_id": job_id, "stop_at_ns": stop_at_ns}
    return encode_envelope(
        node.config, {"map_id": "map-1", "session_id": session_id},
        "stop_mapping", 1, payload, sent_at_ns=node.clock.time_ns(),
    )


def messages(node, message_type=None):
    decoded = [msgpack.unpackb(item[0], raw=False) for item in node.socket.sent]
    return [item for item in decoded if message_type is None or item["message_type"] == message_type]


def last_ack(node):
    return messages(node, "command_ack")[-1]["payload"]


def stamp(stamp_ns):
    return SimpleNamespace(secs=stamp_ns // 1_000_000_000,
                           nsecs=stamp_ns % 1_000_000_000)


def pose_message(stamp_ns, x=0.0):
    return SimpleNamespace(
        header=SimpleNamespace(stamp=stamp(stamp_ns), frame_id="map"),
        child_frame_id="body",
        pose=SimpleNamespace(
            pose=SimpleNamespace(
                position=SimpleNamespace(x=x, y=0.0, z=0.0),
                orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0),
            )
        ),
    )


def cloud_message(stamp_ns, points):
    array = [tuple(point) for point in points]
    return SimpleNamespace(
        header=SimpleNamespace(stamp=stamp(stamp_ns), frame_id="livox_frame"),
        width=len(array), height=1, data=b"x" * (len(array) * 12), points=array,
    )


def mapping_node(duration_ns=1_000_000_000):
    node, clock = make_node(wall_ns=1_000_000_000)
    node.config["min_slice_duration_ns"] = 1
    node.handle_datagram(start_datagram(
        node, start_at_ns=2_000_000_000), GROUND_IP)
    node.session.slice_duration_ns = duration_ns
    node.session.collector.duration_ns = duration_ns
    clock.wall_ns = 2_000_000_000
    node._watchdog()
    node.socket.sent[:] = []
    return node, clock


class NodeTests(unittest.TestCase):
    def test_valid_joint_start_arms_then_starts_at_shared_time(self):
        node, clock = make_node()
        node.handle_datagram(start_datagram(node), GROUND_IP)
        self.assertEqual(node.state, "armed")
        self.assertEqual(node.subscription_count, 2)
        self.assertTrue(last_ack(node)["accepted"])
        clock.wall_ns = 1_999_999_999
        node._watchdog()
        self.assertFalse(node.session.mapping_started)
        clock.wall_ns = 2_000_000_000
        node._watchdog()
        self.assertTrue(node.session.mapping_started)
        self.assertEqual(node.state, "mapping")

    def test_old_start_is_rejected_without_subscribing(self):
        node, unused_clock = make_node()
        node.handle_datagram(start_datagram(node, joint=False), GROUND_IP)
        self.assertEqual(node.state, "standby")
        self.assertEqual(node.subscription_count, 0)
        self.assertEqual(last_ack(node)["error_code"], "COLLABORATION_REQUIRED")

    def test_duplicate_request_replays_ack_without_duplicate_subscribers(self):
        node, unused_clock = make_node()
        datagram = start_datagram(node)
        node.handle_datagram(datagram, GROUND_IP)
        first_subscribers = tuple(node.session.subscribers)
        node.handle_datagram(datagram, GROUND_IP)
        self.assertEqual(tuple(node.session.subscribers), first_subscribers)
        self.assertEqual(len(messages(node, "command_ack")), 2)

    def test_second_session_is_rejected_busy(self):
        node, unused_clock = make_node()
        node.handle_datagram(start_datagram(node), GROUND_IP)
        node.handle_datagram(start_datagram(node, "request-2", "session-2"), GROUND_IP)
        self.assertEqual(last_ack(node)["error_code"], "BUSY")
        self.assertEqual(node.session.identity["session_id"], "session-1")

    def test_start_requires_minimum_lead(self):
        node, unused_clock = make_node()
        node.handle_datagram(start_datagram(node, start_at_ns=1_100_000_000), GROUND_IP)
        self.assertEqual(last_ack(node)["error_code"], "START_LEAD_TOO_SHORT")
        self.assertEqual(node.state, "standby")

    def test_unsynchronized_command_clock_is_rejected(self):
        node, unused_clock = make_node()
        node.handle_datagram(start_datagram(node, sent_at_ns=10_000_000_000), GROUND_IP)
        self.assertEqual(last_ack(node)["error_code"], "CLOCK_UNSYNCED")

    def test_missing_pose_topic_is_rejected(self):
        config = load_config(CONFIG, DEVICE)
        node, unused_clock = make_node(topics=[
            (config["cloud_topic"], config["cloud_message_type"]),
        ])
        node.handle_datagram(start_datagram(node), GROUND_IP)
        self.assertEqual(last_ack(node)["error_code"], "POSE_UNAVAILABLE")

    def test_unexpected_source_is_ignored(self):
        node, unused_clock = make_node()
        node.handle_datagram(start_datagram(node), "192.168.151.99")
        self.assertEqual(node.socket.sent, [])
        self.assertEqual(node.state, "standby")

    def test_missed_armed_start_resets_to_standby(self):
        node, clock = make_node()
        node.handle_datagram(start_datagram(node), GROUND_IP)
        clock.wall_ns = 2_100_000_001
        node._watchdog()
        self.assertEqual(node.state, "standby")
        self.assertEqual(node.subscription_count, 0)
        self.assertEqual(messages(node, "session_status")[-1]["payload"]["error_code"], "START_TIME_MISSED")

    def test_cloud_is_interpolated_binned_and_uploaded_after_lateness(self):
        node, clock = mapping_node()
        node._pose_callback(node.session.token, pose_message(2_060_000_000, x=0.0))
        node._pose_callback(node.session.token, pose_message(2_140_000_000, x=2.0))
        node._cloud_callback(node.session.token, cloud_message(
            2_100_000_000, [[1.0, 0.0, 0.0]]))
        clock.wall_ns = 3_199_999_999
        node._watchdog()
        self.assertEqual(messages(node, "cloud_chunk"), [])
        clock.wall_ns = 3_200_000_000
        node._watchdog()
        chunks = messages(node, "cloud_chunk")
        self.assertTrue(chunks)
        self.assertEqual(chunks[0]["payload"]["slice_id"], 0)
        self.assertTrue(chunks[0]["payload"]["pose_interpolated"])
        self.assertAlmostEqual(chunks[0]["payload"]["map_from_body"]["x"], 1.0)
        statuses = messages(node, "session_status")
        self.assertEqual(statuses[-1]["payload"]["event"], "slice_complete")

    def test_empty_slice_sends_status_without_cloud(self):
        node, clock = mapping_node()
        clock.wall_ns = 3_200_000_000
        node._watchdog()
        self.assertEqual(messages(node, "cloud_chunk"), [])
        self.assertEqual(messages(node, "session_status")[-1]["payload"]["event"], "slice_empty")

    def test_mapping_sends_one_hz_session_heartbeat(self):
        node, clock = mapping_node()
        clock.monotonic_value += 0.99
        node._watchdog()
        self.assertEqual(messages(node, "session_heartbeat"), [])
        clock.monotonic_value += 0.01
        node._watchdog()
        heartbeats = messages(node, "session_heartbeat")
        self.assertEqual(len(heartbeats), 1)
        self.assertEqual(heartbeats[0]["payload"]["state"], "mapping")

    def test_unmatched_cloud_is_counted_and_not_uploaded(self):
        node, clock = mapping_node()
        node._cloud_callback(node.session.token, cloud_message(
            2_100_000_000, [[1.0, 0.0, 0.0]]))
        clock.wall_ns = 3_200_000_000
        node._watchdog()
        self.assertEqual(node.session.stats.dropped_sync, 1)
        self.assertEqual(messages(node, "cloud_chunk"), [])

    def test_raw_messages_are_released_and_sequences_are_unique(self):
        node, clock = mapping_node()
        node._pose_callback(node.session.token, pose_message(2_160_000_000))
        node._pose_callback(node.session.token, pose_message(2_240_000_000))
        raw = cloud_message(2_200_000_000, [[float(i), 1.0, 0.0] for i in range(1, 80)])
        node._cloud_callback(node.session.token, raw)
        held_batch = node.session.collector._states[0].frames
        clock.wall_ns = 3_200_000_000
        node._watchdog()
        self.assertEqual(held_batch, [])
        sequences = [item["sequence"] for item in messages(node)]
        self.assertEqual(len(sequences), len(set(sequences)))
        self.assertTrue(all(len(datagram) <= node.config["max_datagram_bytes"]
                            for datagram, unused in node.socket.sent))

    def test_scheduled_stop_uploads_pre_stop_tail_and_resets(self):
        node, clock = mapping_node()
        node._pose_callback(node.session.token, pose_message(2_260_000_000))
        node._pose_callback(node.session.token, pose_message(2_340_000_000))
        node._cloud_callback(node.session.token, cloud_message(
            2_300_000_000, [[1.0, 0.0, 0.0]]))
        node.handle_datagram(stop_datagram(node, 2_500_000_000), GROUND_IP)
        self.assertEqual(node.state, "stopping")
        clock.wall_ns = 2_500_000_000
        node._watchdog()
        chunks = messages(node, "cloud_chunk")
        self.assertEqual({item["payload"]["sample_stamp_ns"] for item in chunks},
                         {2_300_000_000})
        self.assertTrue(chunks[0]["payload"]["partial"])
        self.assertEqual(node.state, "standby")
        self.assertEqual(node.subscription_count, 0)

    def test_input_timeout_uploads_error_tail_then_resets(self):
        node, clock = mapping_node()
        node._pose_callback(node.session.token, pose_message(2_260_000_000))
        node._pose_callback(node.session.token, pose_message(2_340_000_000))
        node._cloud_callback(node.session.token, cloud_message(
            2_300_000_000, [[1.0, 0.0, 0.0]]))
        clock.wall_ns = 2_400_000_000
        clock.monotonic_value += node.config["input_timeout_seconds"] + 0.01
        node._watchdog()
        chunks = messages(node, "cloud_chunk")
        self.assertTrue(chunks[-1]["payload"]["error_tail"])
        self.assertEqual(messages(node, "session_status")[-1]["payload"]["error_code"],
                         "INPUT_TIMEOUT")
        self.assertEqual(node.state, "standby")

    def test_pose_activity_cannot_hide_cloud_timeout(self):
        node, clock = mapping_node()
        clock.monotonic_value += node.config["input_timeout_seconds"] + 0.01
        node._pose_callback(node.session.token, pose_message(2_100_000_000))
        node._watchdog()
        self.assertEqual(messages(node, "session_status")[-1]["payload"]["error_code"],
                         "INPUT_TIMEOUT")
        self.assertEqual(node.state, "standby")

    def test_wrong_session_stop_is_rejected_without_stopping_active_session(self):
        node, unused_clock = mapping_node()
        node.handle_datagram(stop_datagram(
            node, 2_500_000_000, session_id="wrong-session"), GROUND_IP)
        self.assertFalse(last_ack(node)["accepted"])
        self.assertEqual(last_ack(node)["error_code"], "SESSION_MISMATCH")
        self.assertEqual(node.state, "mapping")

    def test_late_stop_reports_missed_and_resets(self):
        node, clock = mapping_node()
        clock.wall_ns = 2_800_000_000
        node.handle_datagram(stop_datagram(node, 2_500_000_000), GROUND_IP)
        statuses = messages(node, "session_status")
        self.assertEqual(statuses[-1]["payload"]["error_code"], "STOP_TIME_MISSED")
        self.assertEqual(statuses[-1]["payload"]["stop_at_ns"], 2_500_000_000)
        self.assertEqual(node.state, "standby")

    def test_frame_after_sealed_window_is_counted_late(self):
        node, clock = mapping_node()
        node._pose_callback(node.session.token, pose_message(2_560_000_000))
        node._pose_callback(node.session.token, pose_message(2_640_000_000))
        clock.wall_ns = 3_200_000_000
        node._watchdog()
        node._cloud_callback(node.session.token, cloud_message(
            2_600_000_000, [[1.0, 0.0, 0.0]]))
        self.assertEqual(node.session.stats.dropped_late, 1)

    def test_resource_overflow_marks_slice_truncated(self):
        node, clock = mapping_node()
        node.session.collector.limits["max_slice_frames"] = 1
        for value in (2_060_000_000, 2_140_000_000, 2_360_000_000, 2_440_000_000):
            node._pose_callback(node.session.token, pose_message(value))
        node._cloud_callback(node.session.token, cloud_message(
            2_100_000_000, [[1.0, 0.0, 0.0]]))
        node._cloud_callback(node.session.token, cloud_message(
            2_400_000_000, [[2.0, 0.0, 0.0]]))
        clock.wall_ns = 3_200_000_000
        node._watchdog()
        status = messages(node, "session_status")[-1]["payload"]
        self.assertTrue(status["truncated"])
        self.assertEqual(status["error_code"], "SLICE_TRUNCATED")

    def test_duplicate_stop_replays_ack_without_double_cleanup(self):
        node, clock = mapping_node()
        command = stop_datagram(node, 2_500_000_000)
        node.handle_datagram(command, GROUND_IP)
        subscribers = tuple(node.session.subscribers)
        node.handle_datagram(command, GROUND_IP)
        self.assertEqual(tuple(node.session.subscribers), subscribers)
        clock.wall_ns = 2_500_000_000
        node._watchdog()
        node.handle_datagram(command, GROUND_IP)
        self.assertEqual(node.state, "standby")
        self.assertEqual(len(messages(node, "command_ack")), 3)

    def test_wall_clock_rollback_errors_and_resets(self):
        node, clock = mapping_node()
        clock.wall_ns = 1_900_000_000
        node._watchdog()
        self.assertEqual(messages(node, "session_status")[-1]["payload"]["error_code"],
                         "CLOCK_UNSYNCED")
        self.assertEqual(node.state, "standby")

    def test_close_releases_active_session(self):
        node, unused_clock = mapping_node()
        node.close()
        self.assertEqual(node.state, "standby")
        self.assertEqual(node.subscription_count, 0)


if __name__ == "__main__":
    unittest.main()
