import socket
import json
import os
import re
import stat
import threading
import time
import uuid

from .config import LEVEL_RATES, ConfigError, load_config
from .protocol import ProtocolError, encode_envelope
from .smoothing import TelemetrySampler, read_path


class RosUdpTelemetryNode(object):
    def __init__(self, rospy, config):
        self.rospy = rospy
        self.config = config
        namespace = "/epgeneral_udp_telemetry"
        self.config.setdefault("link_status_topic", namespace + "/link/udp_tx")
        self.config.setdefault("diagnostics_topic", namespace + "/diagnostics")
        self.session_id = uuid.uuid4().hex
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.destination = (config["destination_host"], config["destination_port"])
        self.sequences = {"heartbeat": 0, 1: 0, 2: 0, 3: 0}
        self.samplers = {item["name"]: TelemetrySampler(item) for item in config["descriptors"]}
        self.subscribers = []
        self.timers = []
        self.last_send_ok = False
        self.last_send_error = "not sent"
        self.link_publisher = None
        self.diagnostics_publisher = None
        self.sample_states = {item["name"]: None for item in config["descriptors"]}
        self.level_stats = {
            level: {"sent_count": 0, "failure_count": 0, "byte_count": 0}
            for level in LEVEL_RATES
        }
        self.send_lock = threading.Lock()

    def start(self):
        import roslib.message
        from diagnostic_msgs.msg import DiagnosticArray
        from std_msgs.msg import Bool

        self.link_publisher = self.rospy.Publisher(
            self.config["link_status_topic"], Bool, queue_size=1, latch=True)
        self.diagnostics_publisher = self.rospy.Publisher(
            self.config["diagnostics_topic"], DiagnosticArray, queue_size=1, latch=False)

        for descriptor in self.config["descriptors"]:
            source = descriptor["source"]
            if source.get("kind") == "pgm_file":
                self.rospy.loginfo("UDP telemetry file source name=%s state=%s root=%s",
                                   descriptor["name"], source["state_file"], source["map_root"])
                continue
            if descriptor["type"] in {"availability", "pointcloud_status"}:
                message_class = self.rospy.AnyMsg
            else:
                message_class = roslib.message.get_message_class(source["message_type"])
                if message_class is None:
                    raise ConfigError("ROS message type is unavailable: %s" % source["message_type"])
            callback = self._callback_for(descriptor)
            self.subscribers.append(self.rospy.Subscriber(source["topic"], message_class, callback, queue_size=50))
            self.rospy.loginfo(
                "UDP telemetry source name=%s type=%s level=%d topic=%s message_type=%s mapping=%s",
                descriptor["name"], descriptor["type"], descriptor["level"], source["topic"],
                source.get("message_type", "AnyMsg"), source.get("mapping", {}))
        self.timers.append(self.rospy.Timer(self.rospy.Duration(1.0), self._send_heartbeat))
        self.timers.append(self.rospy.Timer(self.rospy.Duration(1.0), self._publish_link_status))
        for level, rate in LEVEL_RATES.items():
            self.timers.append(self.rospy.Timer(self.rospy.Duration(1.0 / rate), lambda event, selected=level: self._send_level(selected)))
        self.rospy.on_shutdown(self.close)
        self.rospy.loginfo(
            "ROS UDP telemetry started device=%s session=%s destination=%s:%d descriptor_hash=%s rates=%s",
            self.config["device_id"], self.session_id, self.destination[0], self.destination[1],
            self.config["descriptor_hash"], LEVEL_RATES)

    def _callback_for(self, descriptor):
        sampler = self.samplers[descriptor["name"]]
        data_type = descriptor["type"]
        source = descriptor["source"]
        if data_type in {"availability", "pointcloud_status"}:
            def touch_callback(message):
                sampler.touch(time.monotonic())
                self._record_sample_result(descriptor, True, "")
            return touch_callback
        if data_type == "text_status":
            value_path = source.get("mapping", {}).get("value", "data")

            def text_callback(message):
                try:
                    value = str(read_path(message, value_path)).strip()
                    accepted = sampler.add({"value": value[:128]}, time.monotonic())
                    self._record_sample_result(descriptor, accepted, sampler.last_rejection_reason)
                except (AttributeError, TypeError, ValueError) as exc:
                    sampler.reject(exc)
                    self._record_sample_result(descriptor, False, exc)
                    self.rospy.logwarn_throttle(5.0, "%s mapping failed: %s" % (descriptor["name"], exc))
            return text_callback
        if data_type == "pose":
            mapping = source.get("mapping", {})
            position_path = mapping.get("position", "pose.position")
            orientation_path = mapping.get("orientation", "pose.orientation")

            def pose_callback(message):
                try:
                    position = read_path(message, position_path)
                    orientation = read_path(message, orientation_path)
                    accepted = sampler.add({
                        "x": float(position.x), "y": float(position.y), "z": float(position.z),
                        "quaternion": (float(orientation.x), float(orientation.y), float(orientation.z), float(orientation.w)),
                    }, time.monotonic())
                    self._record_sample_result(descriptor, accepted, sampler.last_rejection_reason)
                except (AttributeError, TypeError, ValueError) as exc:
                    sampler.reject(exc)
                    self._record_sample_result(descriptor, False, exc)
                    self.rospy.logwarn_throttle(5.0, "%s mapping failed: %s" % (descriptor["name"], exc))
            return pose_callback
        mapping = source.get("mapping", {})

        def imu_callback(message):
            try:
                orientation = read_path(message, mapping.get("orientation", "orientation"))
                angular = read_path(message, mapping.get("angular_velocity", "angular_velocity"))
                linear = read_path(message, mapping.get("linear_acceleration", "linear_acceleration"))
                accepted = sampler.add({
                    "quaternion": (float(orientation.x), float(orientation.y), float(orientation.z), float(orientation.w)),
                    "angular_velocity_x": float(angular.x), "angular_velocity_y": float(angular.y), "angular_velocity_z": float(angular.z),
                    "linear_acceleration_x": float(linear.x), "linear_acceleration_y": float(linear.y), "linear_acceleration_z": float(linear.z),
                }, time.monotonic())
                self._record_sample_result(descriptor, accepted, sampler.last_rejection_reason)
            except (AttributeError, TypeError, ValueError) as exc:
                sampler.reject(exc)
                self._record_sample_result(descriptor, False, exc)
                self.rospy.logwarn_throttle(5.0, "%s mapping failed: %s" % (descriptor["name"], exc))
        return imu_callback

    def _record_sample_result(self, descriptor, accepted, reason):
        name = descriptor["name"]
        previous = self.sample_states[name]
        self.sample_states[name] = bool(accepted)
        if accepted and previous is None:
            self.rospy.loginfo("UDP telemetry source first valid sample: %s", name)
        elif accepted and previous is False:
            self.rospy.loginfo_throttle(5.0, "UDP telemetry source recovered: %s" % name)
        elif not accepted:
            self.rospy.logwarn_throttle(
                5.0, "UDP telemetry source rejected name=%s reason=%s" % (name, reason))

    def _send_heartbeat(self, event):
        self._send("heartbeat", self.sequences["heartbeat"], None, None)
        self.sequences["heartbeat"] += 1

    def _send_level(self, level):
        now = time.monotonic()
        payload = {}
        for descriptor in self.config["descriptors"]:
            if descriptor["level"] != level:
                continue
            sampler = self.samplers[descriptor["name"]]
            try:
                if descriptor["source"].get("kind") == "pgm_file":
                    payload[descriptor["name"]] = self._pgm_file_snapshot(descriptor)
                else:
                    payload[descriptor["name"]] = sampler.snapshot(now)
            except Exception as exc:
                sampler.reject("snapshot failed: %s" % exc, received=False)
                payload[descriptor["name"]] = {"valid": False, "sample_age_seconds": None}
                self._record_sample_result(descriptor, False, exc)
        self._send("telemetry", self.sequences[level], level, payload)
        self.sequences[level] += 1

    @staticmethod
    def _pgm_file_snapshot(descriptor):
        source = descriptor["source"]
        state_path = os.path.abspath(os.path.expanduser(source["state_file"]))
        root = os.path.abspath(os.path.expanduser(source["map_root"]))
        try:
            with open(state_path, "r") as stream:
                state_value = json.load(stream)
            map_id = state_value.get("map_id")
            if not isinstance(map_id, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", map_id):
                raise ValueError("active map id is invalid")
            map_dir = os.path.abspath(os.path.join(root, map_id))
            if os.path.commonpath([root, map_dir]) != root or os.path.islink(map_dir):
                raise ValueError("active map path is unsafe")
            pgm_path = os.path.join(map_dir, "map.pgm")
            file_stat = os.lstat(pgm_path)
            available = stat.S_ISREG(file_stat.st_mode) and not stat.S_ISLNK(file_stat.st_mode)
            return {"valid": True, "status": "available" if available else "unavailable",
                    "sample_age_seconds": 0.0, "map_id": map_id}
        except (IOError, OSError, ValueError, TypeError):
            map_id = locals().get("map_id")
            return {"valid": True, "status": "unavailable", "sample_age_seconds": 0.0,
                    "map_id": map_id if isinstance(map_id, str) else None}

    def _send(self, message_type, sequence, level, payload):
        with self.send_lock:
            try:
                encoded = encode_envelope(self.config, self.session_id, message_type, sequence, payload, level)
                self.socket.sendto(encoded, self.destination)
                self.last_send_ok = True
                self.last_send_error = ""
                if level in self.level_stats:
                    self.level_stats[level]["sent_count"] += 1
                    self.level_stats[level]["byte_count"] += len(encoded)
            except (OSError, ProtocolError) as exc:
                self.last_send_ok = False
                self.last_send_error = str(exc)
                if level in self.level_stats:
                    self.level_stats[level]["failure_count"] += 1
                self.rospy.logerr_throttle(5.0, "UDP send failed: %s" % exc)

    def _publish_link_status(self, event):
        from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
        from std_msgs.msg import Bool

        with self.send_lock:
            last_send_ok = self.last_send_ok
            last_send_error = self.last_send_error
            level_statistics = {
                level: dict(stats) for level, stats in self.level_stats.items()
            }
        self.link_publisher.publish(Bool(data=last_send_ok))
        report = DiagnosticArray()
        report.header.stamp = self.rospy.Time.now()
        status = DiagnosticStatus()
        status.name = "epgeneral_udp_telemetry/udp_tx"
        status.hardware_id = self.config["device_id"]
        status.level = DiagnosticStatus.OK if last_send_ok else DiagnosticStatus.ERROR
        status.message = "sendto succeeded" if last_send_ok else last_send_error
        status.values = [
            KeyValue(key="destination", value="%s:%d" % self.destination),
            KeyValue(key="session_id", value=self.session_id),
            KeyValue(key="descriptor_hash", value=self.config["descriptor_hash"]),
        ]
        for level in sorted(level_statistics):
            stats = level_statistics[level]
            status.values.extend([
                KeyValue(key="level_%d_sent_count" % level, value=str(stats["sent_count"])),
                KeyValue(key="level_%d_failure_count" % level, value=str(stats["failure_count"])),
                KeyValue(key="level_%d_byte_count" % level, value=str(stats["byte_count"])),
                KeyValue(key="level_%d_next_sequence" % level, value=str(self.sequences[level])),
            ])
        report.status = [status]
        now = time.monotonic()
        source_statistics = {}
        for descriptor in self.config["descriptors"]:
            source_status = DiagnosticStatus()
            source_status.name = "epgeneral_udp_telemetry/source/%s" % descriptor["name"]
            source_status.hardware_id = self.config["device_id"]
            stats = self.samplers[descriptor["name"]].statistics(now)
            source_statistics[descriptor["name"]] = stats
            if stats["accepted_count"] == 0:
                source_status.level = DiagnosticStatus.WARN
                source_status.message = "waiting for valid sample"
            elif self.sample_states[descriptor["name"]] is False:
                source_status.level = DiagnosticStatus.WARN
                source_status.message = stats["last_rejection_reason"] or "latest sample rejected"
            else:
                source_status.level = DiagnosticStatus.OK
                source_status.message = "receiving valid samples"
            age = stats["last_sample_age_seconds"]
            source = descriptor["source"]
            source_status.values = [
                KeyValue(key="topic", value=source.get("topic", source.get("state_file", "file"))),
                KeyValue(key="message_type", value=source.get("message_type", "AnyMsg")),
                KeyValue(key="level", value=str(descriptor["level"])),
                KeyValue(key="received_count", value=str(stats["received_count"])),
                KeyValue(key="accepted_count", value=str(stats["accepted_count"])),
                KeyValue(key="rejected_count", value=str(stats["rejected_count"])),
                KeyValue(key="last_sample_age_seconds", value="unknown" if age is None else "%.3f" % age),
                KeyValue(key="last_rejection_reason", value=stats["last_rejection_reason"]),
            ]
            report.status.append(source_status)
        self.diagnostics_publisher.publish(report)
        summary = ", ".join(
            "%s rx=%d ok=%d rejected=%d age=%s" % (
                descriptor["name"],
                source_statistics[descriptor["name"]]["received_count"],
                source_statistics[descriptor["name"]]["accepted_count"],
                source_statistics[descriptor["name"]]["rejected_count"],
                "unknown" if source_statistics[descriptor["name"]]["last_sample_age_seconds"] is None
                else "%.2fs" % source_statistics[descriptor["name"]]["last_sample_age_seconds"],
            ) for descriptor in self.config["descriptors"])
        self.rospy.loginfo_throttle(30.0, "UDP telemetry source summary: %s" % summary)

    def close(self):
        for timer in self.timers:
            timer.shutdown()
        self.timers = []
        try:
            self.socket.close()
        except OSError:
            pass
        self.rospy.loginfo("ROS UDP telemetry stopped")


def run():
    import rospy
    import rospkg

    rospy.init_node("epgeneral_udp_telemetry")
    rospack = rospkg.RosPack()
    device_package_path = rospack.get_path("epgeneral_device_config")
    telemetry_path = rospy.get_param(
        "~telemetry_config_file",
        device_package_path + "/config/udp_telemetry.yaml")
    device_path = rospy.get_param("~device_config_file", device_package_path + "/config/device.yaml")
    config = load_config(telemetry_path, device_path)
    config["destination_host"] = rospy.get_param("~destination_host", config["destination_host"])
    config["destination_port"] = int(rospy.get_param("~destination_port", config["destination_port"]))
    namespace = "/epgeneral_udp_telemetry"
    config["link_status_topic"] = rospy.get_param("~link_status_topic", namespace + "/link/udp_tx")
    config["diagnostics_topic"] = rospy.get_param("~diagnostics_topic", namespace + "/diagnostics")
    node = RosUdpTelemetryNode(rospy, config)
    node.start()
    rospy.spin()
