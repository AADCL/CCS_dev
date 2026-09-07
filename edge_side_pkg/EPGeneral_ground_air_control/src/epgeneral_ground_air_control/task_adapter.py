"""Ground-Air AGV adapter for CCS task execution commands.

The UDP coordinator owns transfer and persistence.  This adapter starts the
vehicle's navigation and mission layer on demand and bridges the generic CCS
execution messages to the existing Ground-Air ROS services.
"""
from __future__ import absolute_import

import json
import math
import os
import signal
import subprocess
import tempfile
import threading
import time

from epgeneral_task_control.scout_adapter import (
    load_localized_map_state,
    validate_trajectory,
    validate_waypoints_on_navigation_map,
)
from epgeneral_task_control.storage import TrajectoryStore


class GroundAirAdapterError(ValueError):
    def __init__(self, message, error_code="INTERNAL_ERROR"):
        ValueError.__init__(self, message)
        self.error_code = error_code


def validate_ground_trajectory(payload, task_id, subtask_id, device_id, map_frame,
                               max_linear_speed_mps):
    validate_trajectory(payload, task_id, subtask_id, device_id, map_frame)
    task_type = str(payload.get("task_type", "ground")).strip().lower()
    if task_type not in ("", "unspecified", "ground", "ground_nav"):
        raise GroundAirAdapterError(
            "Ground-Air AGV accepts ground tasks only", "UNSUPPORTED_TASK_TYPE")
    speed = float(payload["cruise_speed_mps"])
    if speed > float(max_linear_speed_mps) + 1e-9:
        raise GroundAirAdapterError(
            "requested cruise speed %.3f exceeds AGV limit %.3f m/s" %
            (speed, float(max_linear_speed_mps)), "SPEED_LIMIT_EXCEEDED")
    return payload


def feedback_error_code(error):
    explicit = getattr(error, "error_code", "")
    if explicit:
        return explicit
    message = str(error).lower()
    if "localiz" in message or "map<-odom" in message or "tf " in message:
        return "LOCALIZATION_UNAVAILABLE"
    if "map" in message:
        return "MAP_FRAME_MISMATCH"
    if "offboard" in message or "arm" in message or "ground mode" in message:
        return "VEHICLE_NOT_READY"
    if "emergency" in message:
        return "EMERGENCY_STOP_ACTIVE"
    if "service" in message or "process" in message:
        return "TASK_STACK_UNAVAILABLE"
    return "INTERNAL_ERROR"


def mission_progress(state, waypoint_index, waypoint_count):
    count = max(0, int(waypoint_count))
    index = max(0, int(waypoint_index))
    if state == "completed":
        return 1.0
    if not count:
        return 0.0
    return min(1.0, float(index) / float(count))


class GroundAirTaskAdapter(object):
    def __init__(self, rospy, config, command_class, feedback_class,
                 process_factory=subprocess.Popen):
        self.rospy = rospy
        self.config = config
        self.command_class = command_class
        self.feedback_class = feedback_class
        self.process_factory = process_factory
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.feedback_pub = None
        self.tf_buffer = None
        self.tf_listener = None
        self.vehicle_status = None
        self.mission_status = None
        self.prepared = None
        self.execution = None
        self.task_process = None
        self.prepare_worker = None
        self.execution_worker = None
        self.monitor_timer = None
        self.waypoint_started_at = 0.0
        self._service_types = {}
        self._restoring_lock = False

    def start(self):
        import tf2_ros
        from ground_air_msgs.msg import MissionStatus, VehicleStatus
        from ground_air_msgs.srv import SetEmergencyStop, SubmitMission
        from std_srvs.srv import Trigger

        self._service_types = {
            "prepare": Trigger, "submit": SubmitMission, "start": Trigger,
            "cancel": Trigger, "emergency": SetEmergencyStop,
        }
        self._restoring_lock = self._lock_is_persisted()
        self.feedback_pub = self.rospy.Publisher(
            self.config["feedback_topic"], self.feedback_class, queue_size=20)
        self.tf_buffer = tf2_ros.Buffer(cache_time=self.rospy.Duration(10.0))
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)
        self.rospy.Subscriber(
            self.config["vehicle_status_topic"], VehicleStatus,
            self._vehicle_status_callback, queue_size=10)
        self.rospy.Subscriber(
            self.config["mission_status_topic"], MissionStatus,
            self._mission_status_callback, queue_size=20)
        self.rospy.Subscriber(
            self.config["command_topic"], self.command_class,
            self._command_callback, queue_size=20)
        if self._restoring_lock:
            try:
                self._engage_emergency_stop()
            except Exception as exc:
                self.rospy.logerr("failed to restore persisted emergency stop: %s", exc)
            finally:
                self._restoring_lock = False
        self.monitor_timer = self.rospy.Timer(self.rospy.Duration(0.2), self.watchdog)
        self.rospy.on_shutdown(self.close)

    def _vehicle_status_callback(self, message):
        with self.lock:
            self.vehicle_status = message
            if (not self._restoring_lock
                    and not bool(getattr(message, "emergency_stop", False))
                    and self._lock_is_persisted()):
                self._persist_lock(False)

    def _mission_status_callback(self, message):
        with self.lock:
            execution = self.execution
            if execution is None or str(message.mission_id) != execution["mission_id"]:
                return
            states = {
                0: "scheduled", 1: "running", 2: "running", 3: "running",
                4: "completed", 5: "failed", 6: "stopped", 7: "running",
            }
            state = states.get(int(message.state))
            if state is None:
                return
            index = int(message.current_index)
            if index != execution.get("waypoint_index"):
                execution["waypoint_index"] = index
                self.waypoint_started_at = time.monotonic()
            progress = mission_progress(state, index, int(message.total_goals))
            error = "NAVIGATION_ACTION_FAILED" if state == "failed" else ""
            self._feedback(execution["command"], state, index, progress,
                           message.detail, error)
            if state in ("completed", "failed", "stopped"):
                self.execution = None

    def _command_callback(self, command):
        if command.action == self.command_class.PREPARE:
            self._prepare(command)
        elif command.action == self.command_class.SCHEDULE:
            self._schedule(command)
        elif command.action in (self.command_class.CANCEL, self.command_class.STOP):
            self._stop(command)
        elif command.action == self.command_class.UNLOAD:
            self._unload(command)
        elif command.action == self.command_class.EMERGENCY_STOP:
            self._emergency_stop(command)

    def _load_payload(self, command):
        payload = TrajectoryStore(self.config["storage_directory"]).load_payload(
            command.task_id, command.subtask_id)
        validate_ground_trajectory(
            payload, command.task_id, command.subtask_id, self.config["device_id"],
            self.config["map_frame"], self.config["max_linear_speed_mps"])
        if int(payload.get("revision", 0)) != int(command.revision):
            raise GroundAirAdapterError(
                "trajectory revision does not match command", "REVISION_MISMATCH")
        if command.map_id and command.map_id != payload["map_id"]:
            raise GroundAirAdapterError(
                "localized map does not match task map", "MAP_FRAME_MISMATCH")
        state = load_localized_map_state(self.config["active_map_state_file"])
        if state["map_id"] != payload["map_id"]:
            raise GroundAirAdapterError(
                "localized map does not match task map", "MAP_FRAME_MISMATCH")
        map_yaml = os.path.join(
            os.path.expanduser(self.config["navigation_map_root"]),
            payload["map_id"], self.config["navigation_map_yaml"])
        if not os.path.isfile(map_yaml):
            raise GroundAirAdapterError("navigation map yaml is missing", "MAP_FRAME_MISMATCH")
        validate_waypoints_on_navigation_map(payload, map_yaml)
        self._require_live_localization()
        return payload

    def _require_live_localization(self):
        if not bool(self.rospy.get_param(self.config["localization_param"], False)):
            raise GroundAirAdapterError(
                "live localization parameter is false", "LOCALIZATION_UNAVAILABLE")
        if self.tf_buffer is None or not self.tf_buffer.can_transform(
                self.config["map_frame"], "odom", self.rospy.Time(0),
                self.rospy.Duration(0.2)):
            raise GroundAirAdapterError(
                "live map<-odom TF is unavailable", "LOCALIZATION_UNAVAILABLE")

    def _prepare(self, command):
        with self.lock:
            if self.execution is not None:
                self._feedback(command, "failed", -1, 0.0,
                               "an execution is active", "BUSY")
                return
            if self.prepare_worker is not None and self.prepare_worker.is_alive():
                self._feedback(command, "preparing", -1, 0.0,
                               "task stack preparation is already running")
                return
            self.stop_event.clear()
            self._feedback(command, "preparing", -1, 0.0,
                           "validating Ground-Air task prerequisites")
            self.prepare_worker = threading.Thread(
                target=self._run_prepare, args=(command,), name="ground-air-task-prepare")
            self.prepare_worker.daemon = True
            self.prepare_worker.start()

    def _run_prepare(self, command):
        try:
            payload = self._load_payload(command)
            if self._lock_is_persisted():
                raise GroundAirAdapterError(
                    "emergency stop is latched; operator reset is required",
                    "EMERGENCY_STOP_ACTIVE")
            with self.lock:
                if self.task_process is None or self.task_process.poll() is not None:
                    self._start_task_stack()
            self._wait_task_services()
            self._apply_speed(payload["cruise_speed_mps"])
            with self.lock:
                self.prepared = {"command": command, "payload": payload}
            self._feedback(command, "ready", -1, 0.0, "Ground-Air task stack is ready")
        except Exception as exc:
            with self.lock:
                self.prepared = None
            self._feedback(command, "failed", -1, 0.0, str(exc), feedback_error_code(exc))

    def _start_task_stack(self):
        command = [
            "roslaunch", self.config["task_launch_package"], self.config["task_launch_file"],
            "start_navigation:=true", "start_control:=false", "start_mission:=true",
            "start_ccs_task_adapter:=false",
            "dwell_seconds:=" + str(float(self.config["dwell_seconds"])),
            "goal_frame:=" + self.config["map_frame"],
        ]
        self.rospy.loginfo("starting Ground-Air task stack: %s", " ".join(command))
        self.task_process = self.process_factory(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
            universal_newlines=True, preexec_fn=os.setsid)

    def _wait_task_services(self):
        timeout = float(self.config["navigation_startup_timeout_seconds"])
        for service in (
                self.config["mission_submit_service"], self.config["mission_start_service"],
                self.config["mission_cancel_service"]):
            self.rospy.wait_for_service(service, timeout=timeout)
        if self.task_process is None or self.task_process.poll() is not None:
            raise GroundAirAdapterError(
                "Ground-Air task process exited during startup", "TASK_STACK_UNAVAILABLE")

    def _apply_speed(self, requested):
        try:
            from dynamic_reconfigure.client import Client
            client = Client("/move_base/TebLocalPlannerROS",
                            timeout=float(self.config["service_timeout_seconds"]))
            updated = client.update_configuration({
                "max_vel_x": float(requested),
                "max_vel_x_backwards": float(requested),
                "max_vel_theta": float(self.config["max_angular_speed_rps"]),
            })
        except Exception as exc:
            raise GroundAirAdapterError(
                "cannot apply navigation speed: %s" % exc, "TASK_STACK_UNAVAILABLE")
        if abs(float(updated.get("max_vel_x", -1.0)) - float(requested)) > 1e-6:
            raise GroundAirAdapterError(
                "navigation speed verification failed", "TASK_STACK_UNAVAILABLE")

    def _schedule(self, command):
        with self.lock:
            if self.execution is not None:
                self._feedback(command, "failed", -1, 0.0,
                               "an execution is active", "BUSY")
                return
            try:
                prepared = self.prepared
                if prepared is None:
                    raise GroundAirAdapterError(
                        "task stack is not prepared", "NAVIGATION_NOT_READY")
                payload = self._load_payload(command)
                if payload["task_id"] != prepared["payload"]["task_id"]:
                    raise GroundAirAdapterError(
                        "prepared task identity does not match", "REVISION_MISMATCH")
                self._require_vehicle_ready()
                self._call_trigger(self.config["prepare_ground_service"])
                mission_id = str(command.execution_id)
                self._submit_mission(mission_id, payload)
                scheduled_at = float(command.scheduled_at.to_sec())
                if scheduled_at <= time.time():
                    raise GroundAirAdapterError(
                        "scheduled time is in the past", "CLOCK_UNSYNCED")
                self.execution = {
                    "command": command, "payload": payload, "mission_id": mission_id,
                    "scheduled_at": scheduled_at, "waypoint_index": -1,
                }
                self.stop_event.clear()
                self._feedback(command, "scheduled", -1, 0.0,
                               "ground mission accepted; waiting for UTC start")
                self.execution_worker = threading.Thread(
                    target=self._run_scheduled, name="ground-air-task-execution")
                self.execution_worker.daemon = True
                self.execution_worker.start()
            except Exception as exc:
                self.execution = None
                self._feedback(command, "failed", -1, 0.0,
                               str(exc), feedback_error_code(exc))

    def _run_scheduled(self):
        execution = self.execution
        if execution is None:
            return
        command = execution["command"]
        start_at = execution["scheduled_at"] + float(
            execution["payload"].get("start_delay_seconds", 0.0))
        while time.time() < start_at and not self.stop_event.is_set():
            time.sleep(min(0.2, max(0.01, start_at - time.time())))
        if self.stop_event.is_set():
            return
        try:
            self._require_live_localization()
            self._require_vehicle_ready()
            self._call_trigger(self.config["mission_start_service"])
            self.waypoint_started_at = time.monotonic()
            self._feedback(command, "running", 0, 0.0, "ground mission started")
        except Exception as exc:
            with self.lock:
                self.execution = None
            self._feedback(command, "failed", -1, 0.0,
                           str(exc), feedback_error_code(exc))

    def _require_vehicle_ready(self):
        status = self.vehicle_status
        if status is None:
            raise GroundAirAdapterError("vehicle status is unavailable", "VEHICLE_NOT_READY")
        if bool(status.emergency_stop) or self._lock_is_persisted():
            raise GroundAirAdapterError(
                "emergency stop is active", "EMERGENCY_STOP_ACTIVE")
        if not bool(status.localized):
            raise GroundAirAdapterError(
                "vehicle is not localized", "LOCALIZATION_UNAVAILABLE")
        if not bool(status.connected) or not bool(status.armed):
            raise GroundAirAdapterError(
                "manual RC arming is required", "VEHICLE_NOT_READY")
        if str(status.flight_mode).upper() != "OFFBOARD":
            raise GroundAirAdapterError(
                "manual RC OFFBOARD selection is required", "VEHICLE_NOT_READY")
        if int(status.mode) in (2, 3, 4):
            raise GroundAirAdapterError(
                "vehicle is not in a ground-compatible mode", "VEHICLE_NOT_READY")

    def _submit_mission(self, mission_id, payload):
        from geometry_msgs.msg import PoseStamped
        goals = []
        points = payload["waypoints"]
        for index, point in enumerate(points):
            pose = PoseStamped()
            pose.header.frame_id = self.config["map_frame"]
            pose.header.stamp = self.rospy.Time.now()
            pose.pose.position.x = float(point["x"])
            pose.pose.position.y = float(point["y"])
            pose.pose.position.z = float(point["z"])
            target = points[min(index + 1, len(points) - 1)]
            source = points[max(0, index - 1)] if index == len(points) - 1 else point
            yaw = math.atan2(float(target["y"]) - float(source["y"]),
                             float(target["x"]) - float(source["x"]))
            pose.pose.orientation.z = math.sin(yaw / 2.0)
            pose.pose.orientation.w = math.cos(yaw / 2.0)
            goals.append(pose)
        proxy = self._proxy(self.config["mission_submit_service"], "submit")
        response = proxy(mission_id=mission_id, goals=goals)
        if not bool(response.accepted):
            raise GroundAirAdapterError(
                "mission submit rejected: %s" % response.message, "VEHICLE_NOT_READY")

    def _call_trigger(self, name):
        response = self._proxy(name, "prepare" if name == self.config["prepare_ground_service"]
                               else ("start" if name == self.config["mission_start_service"]
                                     else "cancel"))()
        if not bool(response.success):
            raise GroundAirAdapterError(
                "%s rejected: %s" % (name, response.message), "VEHICLE_NOT_READY")
        return response

    def _proxy(self, name, kind):
        self.rospy.wait_for_service(
            name, timeout=float(self.config["service_timeout_seconds"]))
        return self.rospy.ServiceProxy(name, self._service_types[kind])

    def _stop(self, command):
        try:
            if self.task_process is not None and self.task_process.poll() is None:
                self._call_trigger(self.config["mission_cancel_service"])
            with self.lock:
                self.execution = None
                self.stop_event.set()
            self._feedback(command, "stopped", -1, 0.0, "ground mission stopped")
        except Exception as exc:
            self._feedback(command, "failed", -1, 0.0, str(exc), feedback_error_code(exc))

    def _unload(self, command):
        try:
            if self.execution is not None:
                self._call_trigger(self.config["mission_cancel_service"])
            with self.lock:
                self.execution = None
                self.prepared = None
                self.stop_event.set()
            self._stop_task_stack()
            self._feedback(command, "unloaded", -1, 0.0, "Ground-Air task stack unloaded")
        except Exception as exc:
            self._feedback(command, "failed", -1, 0.0, str(exc), feedback_error_code(exc))

    def _emergency_stop(self, command):
        try:
            response = self._engage_emergency_stop()
            with self.lock:
                self.execution = None
                self.prepared = None
                self.stop_event.set()
            self._stop_task_stack()
            self._feedback(command, "emergency_stopped", -1, 0.0,
                           response.message or "emergency stop latched")
        except Exception as exc:
            self._feedback(command, "failed", -1, 0.0, str(exc), feedback_error_code(exc))

    def _engage_emergency_stop(self):
        proxy = self._proxy(self.config["emergency_stop_service"], "emergency")
        response = proxy(active=True)
        confirmed = bool(response.success) and bool(
            getattr(response.status, "emergency_stop", False))
        if not confirmed:
            raise GroundAirAdapterError(
                "emergency stop service did not confirm the latch",
                "EMERGENCY_STOP_FAILED")
        self._persist_lock(True)
        return response

    def _lock_is_persisted(self):
        path = os.path.abspath(os.path.expanduser(self.config["emergency_lock_file"]))
        try:
            with open(path, "r") as stream:
                return bool(json.load(stream).get("active"))
        except (IOError, OSError, TypeError, ValueError):
            return False

    def _persist_lock(self, active):
        path = os.path.abspath(os.path.expanduser(self.config["emergency_lock_file"]))
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        if not active:
            try:
                os.unlink(path)
            except OSError:
                pass
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".estop-", dir=directory)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"schema_version": 1, "active": True}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _feedback(self, command, state, waypoint_index, progress, message, error_code=""):
        feedback = self.feedback_class()
        for key in ("request_id", "task_id", "subtask_id", "device_id", "execution_id"):
            setattr(feedback, key, str(getattr(command, key, "")))
        feedback.revision = int(getattr(command, "revision", 0))
        feedback.state = str(state)
        feedback.waypoint_index = int(waypoint_index)
        payload = self.execution["payload"] if self.execution is not None else (
            self.prepared["payload"] if self.prepared is not None else None)
        feedback.waypoint_count = len(payload["waypoints"]) if payload else 0
        feedback.progress = float(progress)
        feedback.error_code = str(error_code)
        feedback.message = str(message)
        feedback.position.x = feedback.position.y = feedback.position.z = 0.0
        self.feedback_pub.publish(feedback)

    def watchdog(self, unused_event=None):
        with self.lock:
            process = self.task_process
            execution = self.execution
            if process is not None and process.poll() is not None:
                self.task_process = None
                self.prepared = None
                self.execution = None
                if execution is not None:
                    self._feedback(
                        execution["command"], "failed",
                        execution.get("waypoint_index", -1), 0.0,
                        "Ground-Air task process exited", "TASK_STACK_UNAVAILABLE")
                return
            if (execution is not None and self.waypoint_started_at
                    and time.monotonic() - self.waypoint_started_at >
                    float(self.config["waypoint_timeout_seconds"])):
                command = execution["command"]
                self.execution = None
                self.stop_event.set()
                try:
                    self._call_trigger(self.config["mission_cancel_service"])
                except Exception:
                    pass
                self._feedback(command, "failed",
                               execution.get("waypoint_index", -1), 0.0,
                               "ground mission waypoint timed out", "WAYPOINT_TIMEOUT")

    def _stop_task_stack(self):
        process = self.task_process
        self.task_process = None
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGINT)
            process.wait(timeout=10.0)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3.0)

    def close(self):
        self.stop_event.set()
        try:
            if self.execution is not None:
                self._call_trigger(self.config["mission_cancel_service"])
        except Exception:
            pass
        self.execution = None
        self.prepared = None
        self._stop_task_stack()
        if self.monitor_timer is not None:
            self.monitor_timer.shutdown()
            self.monitor_timer = None
