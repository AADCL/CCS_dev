"""Scout Mini navigation adapter for the v2 task execution messages.

The protocol coordinator remains responsible for UDP, persistence and state
transitions.  This module owns only Scout ROS navigation and is deliberately
structured so its trajectory and map checks can be tested without ROS.
"""
from __future__ import absolute_import

import json
import math
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager

import yaml

from .storage import TrajectoryStore
from .control_safety import NavigationControlSafety


class ScoutAdapterError(ValueError):
    def __init__(self, message, error_code=None):
        ValueError.__init__(self, message)
        self.error_code = error_code


def load_localized_map_state(path):
    try:
        with open(os.path.abspath(os.path.expanduser(path)), "r") as stream:
            value = json.load(stream)
    except (IOError, OSError, TypeError, ValueError):
        raise ScoutAdapterError("relocalization state is unavailable")
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise ScoutAdapterError("relocalization state schema is invalid")
    if value.get("status") != "localized" or not isinstance(value.get("map_id"), str):
        raise ScoutAdapterError("Scout is not localized on a usable map")
    transform = value.get("map_from_odom")
    required = ("x", "y", "z", "qx", "qy", "qz", "qw")
    if not isinstance(transform, dict) or any(key not in transform for key in required):
        raise ScoutAdapterError("localized map<-odom transform is missing")
    try:
        values = [float(transform[key]) for key in required]
    except (TypeError, ValueError):
        raise ScoutAdapterError("localized map<-odom transform is invalid")
    if not all(math.isfinite(value) for value in values):
        raise ScoutAdapterError("localized map<-odom transform is non-finite")
    return value


def validate_trajectory(payload, task_id, subtask_id, device_id, map_frame):
    if not isinstance(payload, dict):
        raise ScoutAdapterError("trajectory is invalid")
    for key, expected in (("task_id", task_id), ("subtask_id", subtask_id), ("device_id", device_id)):
        if payload.get(key) != expected:
            raise ScoutAdapterError("trajectory %s does not match execution" % key)
    if payload.get("frame_id") != map_frame or not isinstance(payload.get("map_id"), str):
        raise ScoutAdapterError("trajectory frame or map is invalid")
    waypoints = payload.get("waypoints")
    if not isinstance(waypoints, list) or len(waypoints) < 2:
        raise ScoutAdapterError("trajectory needs at least two waypoints")
    for index, point in enumerate(waypoints):
        if not isinstance(point, dict) or point.get("index") != index:
            raise ScoutAdapterError("trajectory waypoint order is invalid")
        if not all(math.isfinite(float(point[key])) for key in ("x", "y", "z")):
            raise ScoutAdapterError("trajectory waypoint is non-finite")
    delay = float(payload.get("start_delay_seconds", 0.0))
    if not math.isfinite(delay) or delay < 0.0:
        raise ScoutAdapterError("trajectory start delay is invalid")
    return payload


def _read_pgm(path):
    with open(path, "rb") as stream:
        def token():
            value = bytearray()
            while True:
                char = stream.read(1)
                if not char:
                    break
                if char == b"#":
                    stream.readline()
                    continue
                if char.isspace():
                    if value:
                        break
                    continue
                value.extend(char)
            if not value:
                raise ScoutAdapterError("navigation map PGM header is invalid")
            return bytes(value)

        magic = token()
        width, height, maximum = int(token()), int(token()), int(token())
        if width <= 0 or height <= 0 or maximum <= 0 or maximum > 255:
            raise ScoutAdapterError("navigation map PGM dimensions are invalid")
        if magic == b"P5":
            pixels = bytearray(stream.read(width * height))
        elif magic == b"P2":
            pixels = bytearray(int(token()) for unused_index in range(width * height))
        else:
            raise ScoutAdapterError("navigation map PGM encoding is unsupported")
        if len(pixels) != width * height:
            raise ScoutAdapterError("navigation map PGM data is incomplete")
        return width, height, maximum, pixels


def validate_waypoints_on_navigation_map(payload, map_yaml_path):
    try:
        with open(map_yaml_path, "r") as stream:
            metadata = yaml.safe_load(stream)
        image = metadata["image"]
        resolution = float(metadata["resolution"])
        origin = metadata["origin"]
        origin_x, origin_y = float(origin[0]), float(origin[1])
        origin_yaw = float(origin[2])
        negate = int(metadata.get("negate", 0))
        free_threshold = float(metadata.get("free_thresh", 0.196))
    except (IOError, OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise ScoutAdapterError("navigation map metadata is invalid", "MAP_FRAME_MISMATCH")
    if resolution <= 0.0 or abs(origin_yaw) > 1e-6 or negate not in (0, 1):
        raise ScoutAdapterError("navigation map metadata is unsupported", "MAP_FRAME_MISMATCH")
    image_path = image if os.path.isabs(image) else os.path.join(os.path.dirname(map_yaml_path), image)
    width, height, maximum, pixels = _read_pgm(image_path)
    for index, point in enumerate(payload["waypoints"]):
        column = int(math.floor((float(point["x"]) - origin_x) / resolution))
        grid_row = int(math.floor((float(point["y"]) - origin_y) / resolution))
        row = height - 1 - grid_row
        if column < 0 or column >= width or row < 0 or row >= height:
            raise ScoutAdapterError(
                "waypoint %d is outside the navigation map" % index, "WAYPOINT_NOT_TRAVERSABLE")
        color = float(pixels[row * width + column]) / float(maximum)
        occupancy = color if negate else 1.0 - color
        if occupancy >= free_threshold:
            raise ScoutAdapterError(
                "waypoint %d is not in known free space" % index, "WAYPOINT_NOT_TRAVERSABLE")
    return payload


def waypoint_yaws(waypoints, current_x, current_y):
    """Return planar yaw: current pose -> first point, then previous -> current."""
    result = []
    for index, point in enumerate(waypoints):
        if index == 0:
            origin = (current_x, current_y)
        else:
            origin = (waypoints[index - 1]["x"], waypoints[index - 1]["y"])
        result.append(math.atan2(point["y"] - origin[1], point["x"] - origin[0]))
    return result


def navigation_ready_deadline(now, scheduled_at, startup_timeout_seconds):
    """Navigation must be ready both within its timeout and before group start."""
    return min(float(scheduled_at), float(now) + float(startup_timeout_seconds))


def execution_error_code(error):
    explicit = getattr(error, "error_code", None)
    if explicit:
        return explicit
    message = str(error).lower()
    if "navigation process exited" in message:
        return "NAVIGATION_PROCESS_EXITED"
    if "navigation is not prepared" in message:
        return "NAVIGATION_NOT_READY"
    if "move_base action server did not become ready" in message:
        return "NAVIGATION_STARTUP_TIMEOUT"
    if "localized map does not match" in message or "trajectory frame or map" in message:
        return "MAP_FRAME_MISMATCH"
    if any(token in message for token in (
            "relocalization", "not localized", "fastlio", "map<-odom", "map pose", "tf")):
        return "LOCALIZATION_UNAVAILABLE"
    if "map yaml" in message or "navigation map" in message:
        return "MAP_FRAME_MISMATCH"
    if "valid plan" in message or "failed to get a plan" in message:
        return "NAVIGATION_PLAN_FAILED"
    return "INTERNAL_ERROR"


def move_base_failure(state, status_text, waypoint_index):
    text = str(status_text or "").strip()
    detail = "move_base waypoint %d failed with action state %d" % (waypoint_index, state)
    if text:
        detail += ": " + text
    if state == 4 and ("valid plan" in text.lower() or "plan" in text.lower()):
        code = "NAVIGATION_PLAN_FAILED"
    elif state == 5:
        code = "NAVIGATION_GOAL_REJECTED"
    elif state in (2, 8):
        code = "NAVIGATION_GOAL_PREEMPTED"
    else:
        code = "NAVIGATION_ACTION_FAILED"
    return ScoutAdapterError(detail, code)


class ScoutNavigationAdapter(object):
    def __init__(self, rospy, config, command_class, feedback_class,
                 process_factory=subprocess.Popen, action_client_factory=None):
        self.rospy = rospy
        self.config = config
        self.command_class = command_class
        self.feedback_class = feedback_class
        self.process_factory = process_factory
        self.action_client_factory = action_client_factory
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self.prepare_worker = None
        self.execution = None
        self.prepared = None
        self.navigation_process = None
        self.navigation_map_id = None
        self.latest_odom = None
        self.latest_odom_at = 0.0
        self.feedback_pub = None
        self.zero_pub = None
        self.tf_buffer = None
        self.tf_listener = None
        self.tf_converter = None
        self.client = None
        self.monitor_timer = None
        self.terminal_lock = threading.RLock()
        self.stopping = False
        self.closing = False
        self.closed = False
        self.close_permanent = False
        self.close_result = None
        self.control_armed = False
        self.control_safety = None
        if config.get("auto_arm_on_schedule", False) or config.get("auto_disarm_on_terminal", False):
            self.control_safety = NavigationControlSafety(rospy, config, self.stop_event)

    def start(self):
        from geometry_msgs.msg import Twist
        from nav_msgs.msg import Odometry
        import tf2_ros
        import tf2_geometry_msgs

        self.feedback_pub = self.rospy.Publisher(
            self.config["feedback_topic"], self.feedback_class, queue_size=20)
        self.zero_pub = self.rospy.Publisher(
            self.config["zero_velocity_topic"], Twist, queue_size=10)
        self._initialize_tf(tf2_ros, tf2_geometry_msgs)
        if self.control_safety is not None:
            self.control_safety.start(self._reset_emergency_stop)
        self.rospy.Subscriber(
            self.config["command_topic"], self.command_class, self._command_callback, queue_size=10)
        self.rospy.Subscriber(
            self.config["odom_topic"], Odometry, self._odom_callback, queue_size=20)
        self.monitor_timer = self.rospy.Timer(self.rospy.Duration(0.5), self.watchdog)
        self.rospy.on_shutdown(self.close)

    def _initialize_tf(self, tf2_ros, tf2_geometry_msgs):
        self.tf_buffer = tf2_ros.Buffer(cache_time=self.rospy.Duration(10.0))
        # TransformListener owns the /tf and /tf_static subscriptions and must stay alive.
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)
        self.tf_converter = tf2_geometry_msgs

    def _odom_callback(self, message):
        with self.lock:
            self.latest_odom = message
            self.latest_odom_at = time.monotonic()

    def _command_callback(self, command):
        if command.action == self.command_class.PREPARE:
            self._prepare(command)
        elif command.action == self.command_class.SCHEDULE:
            self._schedule(command)
        elif command.action in (self.command_class.CANCEL, self.command_class.STOP):
            self._stop(command)
        elif command.action == self.command_class.UNLOAD:
            self._unload(command)
        elif command.action == getattr(self.command_class, "EMERGENCY_STOP", None):
            self._emergency_stop(command)

    def _reset_emergency_stop(self, unused_request):
        from std_srvs.srv import TriggerResponse
        with self.lock:
            if ((self.closing and not self._can_resume_after_unload()) or
                    self.execution is not None or self.stopping or
                    (self.worker is not None and self.worker.is_alive()) or
                    (self.prepare_worker is not None and self.prepare_worker.is_alive())):
                return TriggerResponse(success=False, message="an execution or transition is active")
            try:
                self.control_safety.clear_latch()
            except (ValueError, IOError, OSError) as exc:
                return TriggerResponse(success=False, message=str(exc))
        return TriggerResponse(success=True, message="emergency stop cleared; control remains disabled")

    def _can_resume_after_unload(self):
        return (self.closed and not self.close_permanent and
                not self._ros_shutdown_requested())

    def _prepare(self, command):
        with self.lock:
            if self.closing and not self._can_resume_after_unload():
                self._feedback(command, "failed", -1, 0.0, "navigation adapter is closing", "BUSY")
                return
            if (self.execution is not None or self.stopping or
                    (self.worker is not None and self.worker.is_alive())):
                self._feedback(command, "failed", -1, 0.0, "an execution is active", "BUSY")
                return
            try:
                if self.control_safety is not None:
                    self.control_safety.assert_unlatched()
                store = TrajectoryStore(self.config["storage_directory"])
                payload = store.load_payload(command.task_id, command.subtask_id)
                validate_trajectory(payload, command.task_id, command.subtask_id,
                                    self.config["device_id"], self.config["map_frame"])
                if payload is None or int(payload.get("revision", 0)) != int(command.revision):
                    raise ScoutAdapterError("trajectory revision does not match preparation")
                if command.map_id and command.map_id != payload["map_id"]:
                    raise ScoutAdapterError("localized map does not match task map")
                if self.prepare_worker is not None and self.prepare_worker.is_alive():
                    self._feedback(command, "preparing", -1, 0.0, "navigation preparation is already running")
                    return
                state = load_localized_map_state(self.config["active_map_state_file"])
                if state["map_id"] != payload["map_id"]:
                    raise ScoutAdapterError("localized map does not match task map")
                map_dir = os.path.join(os.path.expanduser(self.config["navigation_map_root"]), payload["map_id"])
                map_yaml = os.path.join(map_dir, self.config["navigation_map_yaml"])
                if not os.path.isfile(map_yaml):
                    raise ScoutAdapterError("navigation map yaml is missing")
                validate_waypoints_on_navigation_map(payload, map_yaml)
                self._map_pose()
                current = self.prepared
                if (current is not None and current["payload"]["map_id"] == payload["map_id"] and
                        not self._process_exited() and self.client is not None):
                    self.prepared = {"command": command, "payload": payload}
                    self._feedback(command, "ready", -1, 0.0, "navigation already prepared")
                    return
                if self.navigation_process is not None:
                    self._stop_navigation()
                    self.client = None
                if self.closed:
                    # A restarted coordinator can prepare again after unloading.
                    self.monitor_timer = self.rospy.Timer(self.rospy.Duration(0.5), self.watchdog)
                    self.closing = False
                    self.closed = False
                    self.close_result = None
                self.stop_event.clear()
                self.navigation_process = self._start_navigation(payload["map_id"])
                self.navigation_map_id = payload["map_id"]
                self.prepared = {"command": command, "payload": payload}
                self._feedback(command, "preparing", -1, 0.0, "waiting for navigation readiness")
                self.prepare_worker = threading.Thread(
                    target=self._run_prepare, args=(command, payload), name="scout-navigation-prepare")
                self.prepare_worker.daemon = True
                self.prepare_worker.start()
            except (ScoutAdapterError, IOError, OSError, ValueError) as exc:
                self._feedback(command, "failed", -1, 0.0, str(exc), execution_error_code(exc))

    def _run_prepare(self, command, payload):
        try:
            client = self._make_action_client()
            deadline = time.monotonic() + float(self.config["navigation_startup_timeout_seconds"])
            next_feedback_at = time.monotonic() + 1.0
            while time.monotonic() < deadline and not self.stop_event.is_set():
                if self._process_exited():
                    raise ScoutAdapterError("navigation process exited during startup")
                if client.wait_for_server(self.rospy.Duration(0.2)):
                    with self.lock:
                        if (self.stop_event.is_set() or self.stopping or self.prepared is None or
                                self.prepared["payload"]["map_id"] != payload["map_id"]):
                            return
                        self.client = client
                    self._feedback(command, "ready", -1, 0.0, "navigation is ready")
                    return
                if time.monotonic() >= next_feedback_at:
                    self._feedback(command, "preparing", -1, 0.0, "waiting for move_base action server")
                    next_feedback_at = time.monotonic() + 1.0
            if not self.stop_event.is_set():
                raise ScoutAdapterError("move_base action server did not become ready")
        except (ScoutAdapterError, IOError, OSError, ValueError) as exc:
            if not self.stop_event.is_set():
                self._feedback(command, "failed", -1, 0.0, str(exc), execution_error_code(exc))
                with self.lock:
                    self.prepared = None
                    self.client = None
                self._stop_navigation()

    def _schedule(self, command):
        with self.lock:
            if self.closing:
                self._feedback(command, "failed", -1, 0.0, "navigation adapter is closing", "BUSY")
                return
            if (self.execution is not None or self.stopping or
                    (self.worker is not None and self.worker.is_alive())):
                return
            try:
                if self.control_safety is not None:
                    self.control_safety.assert_unlatched()
                prepared = self.prepared
                if prepared is None or self.client is None or self._process_exited():
                    raise ScoutAdapterError("navigation is not prepared")
                payload = prepared["payload"]
                validate_trajectory(payload, command.task_id, command.subtask_id,
                                    self.config["device_id"], self.config["map_frame"])
                if int(payload.get("revision", 0)) != int(command.revision):
                    raise ScoutAdapterError("trajectory revision does not match execution")
                if command.map_id and command.map_id != payload["map_id"]:
                    raise ScoutAdapterError("localized map does not match task map")
                scheduled_at = command.scheduled_at.to_sec()
                if scheduled_at <= time.time():
                    raise ScoutAdapterError("scheduled time is in the past")
                self.stop_event.clear()
                self.execution = {
                    "command": command, "payload": payload, "scheduled_at": scheduled_at,
                    "request_id": command.request_id, "waypoint_index": -1,
                    "feedback_state": "scheduled", "progress": 0.0,
                }
                self._feedback(command, "scheduled", -1, 0.0, "navigation ready; execution scheduled")
                self.worker = threading.Thread(target=self._run, args=(self.execution,), name="scout-task-execution")
                self.worker.daemon = True
                self.worker.start()
            except (ScoutAdapterError, IOError, OSError, ValueError) as exc:
                self._feedback(command, "failed", -1, 0.0, str(exc), execution_error_code(exc))

    def _start_navigation(self, map_id):
        if self.config.get("navigation_management", "managed") == "attach":
            self.rospy.loginfo("attaching to navigation for map %s", map_id)
            return None
        map_dir = os.path.join(os.path.expanduser(self.config["navigation_map_root"]), map_id)
        map_yaml = os.path.join(map_dir, self.config["navigation_map_yaml"])
        command = ["roslaunch", self.config["navigation_launch_package"],
                   self.config["navigation_launch_file"], "map_name:=" + map_id,
                   "map_dir:=" + map_dir, "nav_map_yaml:=" + map_yaml,
                   "odom_topic:=" + self.config["odom_topic"],
                   "cmd_vel_topic:=" + self.config["zero_velocity_topic"]]
        self.rospy.loginfo("starting Scout navigation: %s", " ".join(command))
        return self.process_factory(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                    universal_newlines=True, preexec_fn=os.setsid)

    def _run(self, execution=None):
        execution = execution if execution is not None else self.execution
        if execution is None:
            return
        command = execution["command"]
        payload = execution["payload"]
        try:
            client = self.client
            if client is None or self._process_exited():
                raise ScoutAdapterError("navigation is not prepared")
            start_at = execution["scheduled_at"] + float(payload["start_delay_seconds"])
            while time.time() < start_at and not self.stop_event.is_set():
                self._feedback(command, "scheduled", -1, 0.0, "waiting for scheduled start")
                self.stop_event.wait(min(1.0, max(0.0, start_at - time.time())))
            if self.stop_event.is_set():
                return
            current = self._map_pose()
            if self.config.get("auto_arm_on_schedule", False):
                with self._transition_feedback(execution, "confirming control enable"):
                    self.control_safety.arm()
                with self.lock:
                    if self.stop_event.is_set() or self.execution is not execution:
                        return
                    self.control_armed = True
            yaws = waypoint_yaws(payload["waypoints"], current[0], current[1])
            from move_base_msgs.msg import MoveBaseGoal
            for index, (point, yaw) in enumerate(zip(payload["waypoints"], yaws)):
                if self.stop_event.is_set():
                    return
                goal = MoveBaseGoal()
                goal.target_pose.header.stamp = self.rospy.Time.now()
                goal.target_pose.header.frame_id = self.config["map_frame"]
                goal.target_pose.pose.position.x = point["x"]
                goal.target_pose.pose.position.y = point["y"]
                goal.target_pose.pose.position.z = 0.0
                goal.target_pose.pose.orientation.z = math.sin(yaw / 2.0)
                goal.target_pose.pose.orientation.w = math.cos(yaw / 2.0)
                with self.lock:
                    if self.stop_event.is_set() or self.execution is not execution:
                        return
                    if self.control_armed:
                        self.control_safety.assert_running()
                    client.send_goal(goal)
                    execution["feedback_state"] = "running"
                waypoint_deadline = time.monotonic() + float(
                    self.config["waypoint_timeout_seconds"])
                while not client.wait_for_result(self.rospy.Duration(0.2)):
                    if self.stop_event.is_set():
                        return
                    if self._process_exited():
                        raise ScoutAdapterError("navigation process exited")
                    if self.control_armed:
                        self.control_safety.assert_running()
                    if time.monotonic() >= waypoint_deadline:
                        client.cancel_goal()
                        raise ScoutAdapterError("waypoint %d timed out" % index)
                    if time.monotonic() - self.latest_odom_at > float(self.config["pose_timeout_seconds"]):
                        raise ScoutAdapterError("/fastlio_odom or map<-odom pose timed out")
                    self._feedback(command, "running", index - 1,
                                   float(index) / len(payload["waypoints"]), "moving to waypoint")
                state = client.get_state()
                if self.stop_event.is_set():
                    return
                if state != 3:
                    status_text = client.get_goal_status_text() if hasattr(client, "get_goal_status_text") else ""
                    raise move_base_failure(state, status_text, index)
                self._feedback(command, "running", index,
                               float(index + 1) / len(payload["waypoints"]), "waypoint reached")
                execution["waypoint_index"] = index
                execution["progress"] = float(index + 1) / len(payload["waypoints"])
            self._finish(command, "completed", len(payload["waypoints"]) - 1, 1.0,
                         "task completed", expected_execution=execution)
        except Exception as exc:
            with self.lock:
                should_finish = self.execution is execution and not self.stopping
            if should_finish:
                self._finish(command, "failed", execution["waypoint_index"], 0.0, str(exc),
                             execution_error_code(exc), expected_execution=execution)

    def _make_action_client(self):
        if self.action_client_factory is not None:
            return self.action_client_factory()
        import actionlib
        from move_base_msgs.msg import MoveBaseAction
        return actionlib.SimpleActionClient(self.config["navigation_action"], MoveBaseAction)

    def _map_pose(self):
        if self.control_safety is not None:
            self.control_safety.assert_localized()
        with self.lock:
            message = self.latest_odom
            received_at = self.latest_odom_at
        if message is None:
            raise ScoutAdapterError("/fastlio_odom has not published a pose")
        if time.monotonic() - received_at > float(self.config["pose_timeout_seconds"]):
            raise ScoutAdapterError("/fastlio_odom pose timed out")
        try:
            transform = self.tf_buffer.lookup_transform(
                self.config["map_frame"], message.header.frame_id,
                self.rospy.Time(0), self.rospy.Duration(self.config["pose_timeout_seconds"]))
        except Exception as exc:
            raise ScoutAdapterError("map<-odom TF is unavailable: %s" % exc)
        from geometry_msgs.msg import PoseStamped
        stamped = PoseStamped()
        stamped.header = message.header
        stamped.pose = message.pose.pose
        try:
            pose = self.tf_converter.do_transform_pose(stamped, transform).pose
        except Exception as exc:
            raise ScoutAdapterError("map<-odom pose transform failed: %s" % exc)
        values = (pose.position.x, pose.position.y, pose.position.z)
        if not all(math.isfinite(float(value)) for value in values):
            raise ScoutAdapterError("map pose is non-finite")
        return values

    def _process_exited(self):
        if self.config.get("navigation_management", "managed") == "attach":
            return False
        return self.navigation_process is None or self.navigation_process.poll() is not None

    def _stop(self, command):
        if self.control_safety is not None:
            with self.lock:
                self.stopping = True
                self.stop_event.set()
                execution = self.execution
            self._finish(command, "stopped", execution["waypoint_index"] if execution else -1,
                         0.0, "task stopped", force=True)
            return
        with self.lock:
            execution = self.execution
            self.stop_event.set()
            client = getattr(self, "client", None)
        if client is not None:
            client.cancel_all_goals()
        self._publish_zero()
        if execution is not None:
            self._finish(command, "stopped", execution["waypoint_index"], 0.0, "task stopped")
        elif self.control_safety is not None:
            self._finish(command, "stopped", -1, 0.0, "task stopped", force=True)

    def _emergency_stop(self, command):
        if self.control_safety is None:
            self._feedback(command, "failed", -1, 0.0,
                           "confirmed emergency stop is not configured", "EMERGENCY_STOP_UNSUPPORTED")
            return
        self.stop_event.set()
        with self.lock:
            self.stopping = True
        persistence_error = None
        try:
            self.control_safety.latch("operator emergency stop")
        except (ValueError, IOError, OSError) as exc:
            persistence_error = exc
        state = "failed" if persistence_error else "emergency_stopped"
        self._finish(command, state, -1, 0.0,
                     str(persistence_error) if persistence_error else "emergency stop confirmed",
                     execution_error_code(persistence_error) if persistence_error else "", force=True)
        with self.lock:
            self.prepared = None
            self.client = None

    def _unload(self, command):
        if getattr(command, "request_id", "") == "shutdown-unload":
            state, message, error_code = self._close_once(
                shutdown_context=True, permanent=self._ros_shutdown_requested())
            self._feedback(command, state, -1, 0.0, message, error_code, include_pose=False)
            return
        with self.terminal_lock:
            if self.closing:
                return
            self._unload_navigation(command)

    def _unload_navigation(self, command):
        if self.control_safety is not None:
            with self.lock:
                self.stop_event.set()
                self.stopping = True
            self._finish(command, "unloaded", -1, 0.0, "navigation unloaded", force=True)
            self._stop_navigation()
            with self.lock:
                self.prepared = None
                self.client = None
            return
        with self.lock:
            self.stop_event.set()
            client = self.client
            execution = self.execution
        if client is not None:
            client.cancel_all_goals()
        self._publish_zero()
        if execution is not None:
            self._feedback(command, "stopped", execution["waypoint_index"], 0.0, "task unloaded")
        self._stop_navigation()
        with self.lock:
            self.execution = None
            self.prepared = None
            self.client = None
        self._feedback(command, "unloaded", -1, 0.0, "navigation unloaded")

    def _publish_zero(self):
        if self.zero_pub is None:
            return
        from geometry_msgs.msg import Twist
        for unused_index in range(int(self.config["zero_velocity_count"])):
            self.zero_pub.publish(Twist())
            time.sleep(1.0 / float(self.config["zero_velocity_hz"]))

    def _stop_navigation(self):
        process, self.navigation_process = self.navigation_process, None
        self.navigation_map_id = None
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGINT)
            process.wait(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except OSError:
                pass

    def _feedback(self, command, state, waypoint_index, progress, message, error_code="", include_pose=True):
        if self.feedback_pub is None:
            return
        feedback = self.feedback_class()
        feedback.request_id = command.request_id
        feedback.task_id = command.task_id
        feedback.subtask_id = command.subtask_id
        feedback.device_id = command.device_id
        feedback.execution_id = command.execution_id
        feedback.revision = command.revision
        feedback.state = state
        feedback.waypoint_index = int(waypoint_index)
        feedback.waypoint_count = len(self.execution["payload"]["waypoints"]) if self.execution else 0
        feedback.progress = float(progress)
        feedback.error_code = error_code
        feedback.message = str(message)
        x = y = z = 0.0
        if include_pose:
            try:
                x, y, z = self._map_pose()
            except Exception:
                pass
        feedback.position.x, feedback.position.y, feedback.position.z = x, y, z
        self.feedback_pub.publish(feedback)

    @contextmanager
    def _transition_feedback(self, execution, message):
        finished = threading.Event()
        worker = None
        if execution is not None and self.control_safety is not None:
            interval = min(1.0, float(self.config.get("execution_feedback_seconds", 3.0)) / 3.0)

            def heartbeat():
                while not finished.is_set():
                    with self.lock:
                        if self.execution is not execution or finished.is_set():
                            return
                        self._feedback(execution["command"], execution.get("feedback_state", "scheduled"),
                                       execution["waypoint_index"], execution.get("progress", 0.0),
                                       message, include_pose=False)
                    finished.wait(interval)

            worker = threading.Thread(target=heartbeat, name="navigation-control-feedback")
            worker.daemon = True
            worker.start()
        try:
            yield
        finally:
            finished.set()
            if worker is not None:
                worker.join(timeout=1.0)

    def _finish(self, command, state, waypoint_index, progress, message, error_code="", force=False,
                expected_execution=None):
        with self.terminal_lock:
            with self.lock:
                if self.closing:
                    return
                if expected_execution is not None and self.execution is not expected_execution:
                    return
                if self.execution is None and state != "failed" and not force:
                    return
                execution = self.execution
                self.stopping = True
                self.stop_event.set()
                if self.execution is not None:
                    self.execution["waypoint_index"] = waypoint_index
            try:
                with self._transition_feedback(execution, "confirming control stop"):
                    self._finish_control(command, state, waypoint_index, progress, message, error_code)
            finally:
                with self.lock:
                    self.stopping = False

    def _finish_control(self, command, state, waypoint_index, progress, message, error_code):
        if self.control_safety is not None:
            errors = []
            if self.client is not None:
                try:
                    self.client.cancel_all_goals()
                except Exception as exc:
                    errors.append("goal cancellation failed: %s" % exc)
            try:
                self._publish_zero()
            except Exception as exc:
                errors.append("zero velocity publication failed: %s" % exc)
            try:
                self.control_safety.disarm()
            except (ValueError, IOError, OSError) as exc:
                errors.append(str(exc))
                error_code = execution_error_code(exc)
            if errors:
                state, progress, message = "failed", 0.0, message + "; stop: " + "; ".join(errors)
                error_code = error_code or "CONTROL_STOP_FAILED"
                try:
                    self.control_safety.latch(message)
                except (ValueError, IOError, OSError) as exc:
                    message += "; " + str(exc)
            elif state == "completed" and self.control_safety.latched:
                state, message, error_code = "failed", "emergency stop is latched", "EMERGENCY_STOP_LATCHED"
            self.control_armed = False
        with self.lock:
            self._feedback(command, state, waypoint_index, progress, message, error_code,
                           include_pose=self.control_safety is None)
            self.execution = None

    def watchdog(self, unused_event=None):
        with self.lock:
            if self.closing:
                return
            execution = self.execution
            check_control = self.control_armed and not self.stop_event.is_set()
        if check_control:
            try:
                self.control_safety.assert_running()
            except ValueError as exc:
                if execution is not None:
                    self._finish(execution["command"], "failed", execution["waypoint_index"],
                                 0.0, str(exc), execution_error_code(exc), expected_execution=execution)
                return
        if self.control_safety is not None and self.navigation_process is not None and self._process_exited():
            with self.lock:
                execution = self.execution
                record = execution or self.prepared
                process = self.navigation_process
            if record is not None:
                self._finish(record["command"], "failed", -1, 0.0,
                             "navigation process exited", "NAVIGATION_PROCESS_EXITED", force=True,
                             expected_execution=execution)
            with self.lock:
                if self.navigation_process is process:
                    self.prepared = None
                    self.client = None
                    self.navigation_process = None
                    self.navigation_map_id = None
            return
        with self.lock:
            if self.navigation_process is None or not self._process_exited():
                return
            execution = self.execution
            prepared = self.prepared
            self.navigation_process = None
            self.navigation_map_id = None
            self.execution = None
            self.prepared = None
            self.client = None
            self.stop_event.set()
        if execution is not None:
            self._feedback(execution["command"], "failed", execution["waypoint_index"], 0.0,
                           "navigation process exited", "NAVIGATION_PROCESS_EXITED")
        if prepared is not None:
            self._feedback(prepared["command"], "failed", -1, 0.0,
                           "navigation process exited", "NAVIGATION_PROCESS_EXITED")
        self._publish_zero()

    def _ros_shutdown_requested(self):
        # rospy runs on_shutdown callbacks before setting is_shutdown().
        core = getattr(self.rospy, "core", None)
        requested = getattr(core, "is_shutdown_requested", None)
        return (self.rospy.is_shutdown() is True or
                (callable(requested) and requested() is True))

    def close(self):
        return self._close_once(shutdown_context=self._ros_shutdown_requested())

    def _close_once(self, shutdown_context, permanent=True):
        # A coordinator shutdown message can precede this process's ROS signal.
        # Quiesce before waiting for a terminal operation; never wait holding lock.
        with self.lock:
            self.close_permanent = self.close_permanent or permanent
            self.closing = True
            self.stop_event.set()
            timer, self.monitor_timer = self.monitor_timer, None
        if timer is not None:
            timer.shutdown()
        with self.terminal_lock:
            with self.lock:
                if self.closed:
                    return self.close_result
            errors = []
            error_code = ""
            client = self.client
            if client is not None:
                try:
                    client.cancel_all_goals()
                except Exception as exc:
                    errors.append("goal cancellation failed: %s" % exc)
            try:
                self._publish_zero()
            except Exception as exc:
                errors.append("zero velocity publication failed: %s" % exc)
            if self.control_safety is not None:
                try:
                    self.control_safety.disarm(allow_confirmed_disabled=shutdown_context)
                except (ValueError, IOError, OSError) as exc:
                    errors.append(str(exc))
                    error_code = execution_error_code(exc)
            try:
                self._stop_navigation()
            except Exception as exc:
                errors.append("navigation cleanup failed: %s" % exc)
            message = "navigation unloaded"
            state = "unloaded"
            if errors:
                state = "failed"
                message += "; stop: " + "; ".join(errors)
                error_code = error_code or "CONTROL_STOP_FAILED"
                if self.control_safety is not None:
                    try:
                        self.control_safety.latch(message)
                    except (ValueError, IOError, OSError) as exc:
                        message += "; " + str(exc)
                self.rospy.logerr("navigation shutdown failed: %s", message)
            with self.lock:
                self.client = None
                self.prepared = None
                self.execution = None
                self.control_armed = False
                self.stopping = False
                result = (state, message, error_code)
                self.close_result = result
                self.closed = True
            return result



# The implementation is device-neutral; retain the historical name for Scout
# deployments while exposing a generic name to new device profiles.
NavigationAdapter = ScoutNavigationAdapter
