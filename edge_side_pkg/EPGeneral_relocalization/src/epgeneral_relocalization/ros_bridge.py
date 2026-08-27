from __future__ import absolute_import

import math
import os
import signal
import subprocess
import threading
import time


class RosIntegrationError(RuntimeError):
    pass


def rostopic_has_subscriber(output):
    in_subscribers = False
    for raw_line in str(output).splitlines():
        line = raw_line.strip()
        if line == "Subscribers:":
            in_subscribers = True
            continue
        if in_subscribers and line.endswith(":"):
            break
        if in_subscribers and line.startswith("*"):
            return True
    return False


class StackManager(object):
    def __init__(self, config, logger, popen=subprocess.Popen,
                 run_command=subprocess.run, clock=time.monotonic):
        self.config = config
        self.logger = logger
        self.popen = popen
        self.run_command = run_command
        self.clock = clock
        self.processes = []

    def start(self, map_id, map_dir):
        self.stop()
        values = {
            "map_id": map_id, "map_dir": map_dir,
            "map_pcd": os.path.join(map_dir, "public_map.pcd"),
            "map_yaml": os.path.join(map_dir, "map.yaml"),
        }
        try:
            for stage in self.config["stages"]:
                command = ["roslaunch", str(stage["package"]), str(stage["launch"])]
                command.extend(str(item).format(**values) for item in stage.get("args", []))
                self.logger.info("relocalization_stage_start name=%s command=%s", stage["name"], command)
                process = self.popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, preexec_fn=os.setsid,
                )
                self.processes.append((str(stage["name"]), process))
                drain = threading.Thread(
                    target=self._drain_output, args=(str(stage["name"]), process),
                    name="relocalization-%s-log" % stage["name"])
                drain.daemon = True
                drain.start()
                time.sleep(0.5)
                if process.poll() is not None:
                    raise RosIntegrationError("stage %s exited; see relocalization.log" % stage["name"])
            self._wait_topics()
        except Exception:
            self.stop()
            raise

    def _drain_output(self, name, process):
        if process.stdout is None:
            return
        for line in iter(process.stdout.readline, ""):
            text = line.rstrip()
            if text:
                self.logger.info("relocalization_stage_output name=%s output=%s", name, text[:4096])

    def _wait_topics(self):
        deadline = self.clock() + self.config["startup_timeout_seconds"]
        while self.clock() < deadline:
            for name, process in self.processes:
                if process.poll() is not None:
                    raise RosIntegrationError("stage %s exited during readiness" % name)
            ready = True
            for topic in (self.config["initial_pose_topic"], self.config["map_topic"]):
                completed = self.run_command(
                    ["rostopic", "type", topic], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, universal_newlines=True, check=False,
                )
                if completed.returncode != 0:
                    ready = False
                    break
            if ready:
                completed = self.run_command(
                    ["rostopic", "info", self.config["initial_pose_topic"]],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, check=False,
                )
                ready = completed.returncode == 0 and rostopic_has_subscriber(completed.stdout)
            if ready:
                return
            time.sleep(0.2)
        raise RosIntegrationError(
            "relocalization ROS topics or /initialpose subscriber did not become ready")

    def stop(self):
        for unused_name, process in reversed(self.processes):
            if process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGINT)
                except OSError:
                    pass
        deadline = time.monotonic() + 5.0
        for unused_name, process in reversed(self.processes):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except OSError:
                    pass
        self.processes = []


def quaternion_yaw(qx, qy, qz, qw):
    return math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))


def angle_span(values):
    if not values:
        return float("inf")
    unwrapped = [values[0]]
    for value in values[1:]:
        previous = unwrapped[-1]
        while value - previous > math.pi:
            value -= 2 * math.pi
        while value - previous < -math.pi:
            value += 2 * math.pi
        unwrapped.append(value)
    return max(unwrapped) - min(unwrapped)


class RosBridge(object):
    def __init__(self, config, rospy_module, logger):
        self.config = config
        self.rospy = rospy_module
        self.logger = logger
        from geometry_msgs.msg import PoseWithCovarianceStamped
        import tf2_ros
        self.message_class = PoseWithCovarianceStamped
        self.publisher = rospy_module.Publisher(
            config["initial_pose_topic"], PoseWithCovarianceStamped, queue_size=1, latch=False)
        self.buffer = tf2_ros.Buffer(cache_time=rospy_module.Duration(10.0))
        self.listener = tf2_ros.TransformListener(self.buffer)
        self._monitor_lock = threading.Lock()
        self._monitor_generation = 0

    def cancel_monitor(self):
        with self._monitor_lock:
            self._monitor_generation += 1

    def _next_monitor_generation(self):
        with self._monitor_lock:
            self._monitor_generation += 1
            return self._monitor_generation

    def _monitor_is_current(self, generation):
        with self._monitor_lock:
            return generation == self._monitor_generation

    def publish_and_monitor(self, x, y, yaw, covariance, callback):
        generation = self._next_monitor_generation()
        message = self.message_class()
        message.header.stamp = self.rospy.Time.now()
        message.header.frame_id = self.config["map_frame"]
        message.pose.pose.position.x = x
        message.pose.pose.position.y = y
        message.pose.pose.orientation.z = math.sin(yaw / 2.0)
        message.pose.pose.orientation.w = math.cos(yaw / 2.0)
        message.pose.covariance[0] = float(covariance.get("x", 0.25))
        message.pose.covariance[7] = float(covariance.get("y", 0.25))
        message.pose.covariance[35] = float(covariance.get("yaw", (math.pi / 12.0) ** 2))
        self.publisher.publish(message)
        thread = threading.Thread(
            target=self._monitor, args=(callback, generation), name="relocalization-tf")
        thread.daemon = True
        thread.start()

    def _monitor(self, callback, generation):
        deadline = time.monotonic() + self.config["tf_timeout_seconds"]
        samples = []
        delay = 1.0 / self.config["tf_sample_hz"]
        while (time.monotonic() < deadline and not self.rospy.is_shutdown()
               and self._monitor_is_current(generation)):
            try:
                transform = self.buffer.lookup_transform(
                    self.config["map_frame"], self.config["odom_frame"],
                    self.rospy.Time(0), self.rospy.Duration(min(delay, 0.2)))
                t, q = transform.transform.translation, transform.transform.rotation
                sample = (float(t.x), float(t.y), float(t.z),
                          float(q.x), float(q.y), float(q.z), float(q.w))
                if all(math.isfinite(value) for value in sample):
                    samples.append(sample)
                    samples = samples[-self.config["tf_sample_count"]:]
            except Exception:
                pass
            if len(samples) == self.config["tf_sample_count"]:
                translations = [item[:3] for item in samples]
                spans = [max(values) - min(values) for values in zip(*translations)]
                yaws = [quaternion_yaw(*item[3:]) for item in samples]
                if (math.sqrt(sum(value * value for value in spans))
                        <= self.config["translation_tolerance_m"]
                        and math.degrees(angle_span(yaws)) <= self.config["yaw_tolerance_deg"]):
                    if self._monitor_is_current(generation):
                        callback(True, samples[-1], "")
                    return
            time.sleep(delay)
        if self._monitor_is_current(generation):
            callback(False, None, "map<-odom TF did not stabilize before timeout")
