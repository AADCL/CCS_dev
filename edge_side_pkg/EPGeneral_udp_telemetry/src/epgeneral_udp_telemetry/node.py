import socket
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
            if descriptor["type"] in {"availability", "pointcloud_status"}:
                message_class = self.rospy.AnyMsg
            else:
                message_class = roslib.message.get_message_class(source["message_type"])
                if message_class is None:
                    raise ConfigError("ROS message type is unavailable: %s" % source["message_type"])
            callback = self._callback_for(descriptor)
            self.subscribers.append(self.rospy.Subscriber(source["topic"], message_class, callback, queue_size=50))
        self.timers.append(self.rospy.Timer(self.rospy.Duration(1.0), self._send_heartbeat))
        self.timers.append(self.rospy.Timer(self.rospy.Duration(1.0), self._publish_link_status))
        for level, rate in LEVEL_RATES.items():
            self.timers.append(self.rospy.Timer(self.rospy.Duration(1.0 / rate), lambda event, selected=level: self._send_level(selected)))
        self.rospy.on_shutdown(self.close)
        self.rospy.loginfo("ROS UDP telemetry sending to %s:%d", *self.destination)

    def _callback_for(self, descriptor):
        sampler = self.samplers[descriptor["name"]]
        data_type = descriptor["type"]
        source = descriptor["source"]
        if data_type in {"availability", "pointcloud_status"}:
            return lambda message: sampler.touch(time.monotonic())
        if data_type == "text_status":
            value_path = source.get("mapping", {}).get("value", "data")

            def text_callback(message):
                try:
                    value = str(read_path(message, value_path)).strip()
                    sampler.add({"value": value[:128]}, time.monotonic())
                except (AttributeError, TypeError, ValueError) as exc:
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
                    sampler.add({
                        "x": float(position.x), "y": float(position.y), "z": float(position.z),
                        "quaternion": (float(orientation.x), float(orientation.y), float(orientation.z), float(orientation.w)),
                    }, time.monotonic())
                except (AttributeError, TypeError, ValueError) as exc:
                    self.rospy.logwarn_throttle(5.0, "%s mapping failed: %s" % (descriptor["name"], exc))
            return pose_callback
        mapping = source.get("mapping", {})

        def imu_callback(message):
            try:
                orientation = read_path(message, mapping.get("orientation", "orientation"))
                angular = read_path(message, mapping.get("angular_velocity", "angular_velocity"))
                linear = read_path(message, mapping.get("linear_acceleration", "linear_acceleration"))
                sampler.add({
                    "quaternion": (float(orientation.x), float(orientation.y), float(orientation.z), float(orientation.w)),
                    "angular_velocity_x": float(angular.x), "angular_velocity_y": float(angular.y), "angular_velocity_z": float(angular.z),
                    "linear_acceleration_x": float(linear.x), "linear_acceleration_y": float(linear.y), "linear_acceleration_z": float(linear.z),
                }, time.monotonic())
            except (AttributeError, TypeError, ValueError) as exc:
                self.rospy.logwarn_throttle(5.0, "%s mapping failed: %s" % (descriptor["name"], exc))
        return imu_callback

    def _send_heartbeat(self, event):
        self._send("heartbeat", self.sequences["heartbeat"], None, None)
        self.sequences["heartbeat"] += 1

    def _send_level(self, level):
        now = time.monotonic()
        payload = {
            descriptor["name"]: self.samplers[descriptor["name"]].snapshot(now)
            for descriptor in self.config["descriptors"] if descriptor["level"] == level
        }
        self._send("telemetry", self.sequences[level], level, payload)
        self.sequences[level] += 1

    def _send(self, message_type, sequence, level, payload):
        try:
            encoded = encode_envelope(self.config, self.session_id, message_type, sequence, payload, level)
            self.socket.sendto(encoded, self.destination)
            self.last_send_ok = True
            self.last_send_error = ""
        except (OSError, ProtocolError) as exc:
            self.last_send_ok = False
            self.last_send_error = str(exc)
            self.rospy.logerr_throttle(5.0, "UDP send failed: %s" % exc)

    def _publish_link_status(self, event):
        from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
        from std_msgs.msg import Bool

        self.link_publisher.publish(Bool(data=self.last_send_ok))
        report = DiagnosticArray()
        report.header.stamp = self.rospy.Time.now()
        status = DiagnosticStatus()
        status.name = "epgeneral_udp_telemetry/udp_tx"
        status.hardware_id = self.config["device_id"]
        status.level = DiagnosticStatus.OK if self.last_send_ok else DiagnosticStatus.ERROR
        status.message = "sendto succeeded" if self.last_send_ok else self.last_send_error
        status.values = [
            KeyValue(key="destination", value="%s:%d" % self.destination),
            KeyValue(key="session_id", value=self.session_id),
        ]
        report.status = [status]
        self.diagnostics_publisher.publish(report)

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
    package_path = rospack.get_path("epgeneral_udp_telemetry")
    device_package_path = rospack.get_path("epgeneral_device_config")
    telemetry_path = rospy.get_param("~telemetry_config_file", package_path + "/config/telemetry.yaml")
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
