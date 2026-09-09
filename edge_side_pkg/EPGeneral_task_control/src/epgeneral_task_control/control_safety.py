"""Opt-in control interlock for navigation stacks with explicit enable services."""
import json
import os
import tempfile
import threading
import time


class ControlSafetyError(ValueError):
    def __init__(self, message, error_code="CONTROL_SERVICE_UNAVAILABLE"):
        super(ControlSafetyError, self).__init__(message)
        self.error_code = error_code


class NavigationControlSafety(object):
    def __init__(self, rospy, config, stop_event):
        self.rospy = rospy
        self.config = config
        self.stop_event = stop_event
        self.lock = threading.RLock()
        self.transition_lock = threading.Lock()
        self.rpc_lock = threading.Lock()
        self.path = os.path.abspath(os.path.expanduser(config["emergency_stop_state_file"]))
        # Any existing marker, including a damaged one, remains fail-closed.
        self.latched = os.path.lexists(self.path)
        self.localization = (False, 0.0)
        self.control = (False, 0.0)
        self.control_revision = 0
        self.reset_client = None
        self.enable_client = None
        self.reset_service = None

    def start(self, reset_callback):
        from std_msgs.msg import Bool
        from std_srvs.srv import Trigger, SetBool
        self.reset_client = self.rospy.ServiceProxy(
            self.config["navigation_reset_service"], Trigger)
        self.enable_client = self.rospy.ServiceProxy(
            self.config["control_enable_service"], SetBool)
        self.rospy.Subscriber(self.config["localization_ok_topic"], Bool,
                              self.localization_callback, queue_size=10)
        self.rospy.Subscriber(self.config["control_enabled_topic"], Bool,
                              self.control_callback, queue_size=10)
        if self.config.get("control_diagnostics_topic"):
            from diagnostic_msgs.msg import DiagnosticArray
            self.rospy.Subscriber(self.config["control_diagnostics_topic"], DiagnosticArray,
                                  self.diagnostics_callback, queue_size=10)
        self.reset_service = self.rospy.Service("~reset_emergency_stop", Trigger, reset_callback)

    def localization_callback(self, message):
        with self.lock:
            self.localization = (bool(message.data), time.monotonic())

    def control_callback(self, message):
        with self.lock:
            self.control = (bool(message.data), time.monotonic())
            self.control_revision += 1

    def diagnostics_callback(self, message):
        # Native Go2 Bool state is latched and only published on transitions.
        # Its periodic diagnostics can renew, but never contradict, that state.
        try:
            age = float(self.rospy.get_time()) - float(message.header.stamp.to_sec())
            if age < 0.0 or age > float(self.config["control_state_timeout_seconds"]):
                return
            statuses = [item for item in message.status if item.name ==
                        self.config.get("control_diagnostics_status", "GO2 SDK bridge")]
            if len(statuses) != 1:
                return
            values = [item.value for item in statuses[0].values if item.key ==
                      self.config.get("control_diagnostics_key", "motion_enabled")]
            if len(values) != 1 or values[0] not in ("true", "false"):
                return
            value = values[0] == "true"
            produced_at = time.monotonic() - age
            with self.lock:
                if self.control[0] is value and self.control[1] > 0.0 and produced_at >= self.control[1]:
                    self.control = (value, produced_at)
                    self.control_revision += 1
        except (AttributeError, TypeError, ValueError):
            return

    def assert_unlatched(self):
        with self.lock:
            if self.latched:
                raise ControlSafetyError("emergency stop requires manual reset", "EMERGENCY_STOP_LATCHED")

    def assert_localized(self):
        with self.lock:
            value, received = self.localization
        if not value or time.monotonic() - received > float(self.config["pose_timeout_seconds"]):
            raise ControlSafetyError("localization health is false or stale", "LOCALIZATION_UNAVAILABLE")

    def _control_matches(self, expected, since=0.0, after_revision=None):
        with self.lock:
            value, received = self.control
            revision = self.control_revision
        return (value is expected and received > 0.0 and received >= since and
                (after_revision is None or revision > after_revision) and
                time.monotonic() - received <= float(self.config["control_state_timeout_seconds"]))

    def assert_running(self):
        self.assert_unlatched()
        self.assert_localized()
        if not self._control_matches(True):
            raise ControlSafetyError("control enabled state is false or stale", "CONTROL_STATE_UNAVAILABLE")

    def latch(self, reason):
        with self.lock:
            self.latched = True
            self.stop_event.set()
            directory = os.path.dirname(self.path)
            temporary = None
            try:
                os.makedirs(directory, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
                    temporary = stream.name
                    json.dump({"schema_version": 1, "latched": True,
                               "reason": str(reason), "recorded_at": time.time()}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                self._sync_directory(directory)
            except (IOError, OSError) as exc:
                raise ControlSafetyError("cannot persist emergency stop: %s" % exc,
                                         "EMERGENCY_STOP_PERSISTENCE_FAILED")
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)

    @staticmethod
    def _sync_directory(directory):
        if os.name != "nt":
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def clear_latch(self):
        if not self.transition_lock.acquire(False):
            raise ControlSafetyError("a control transition is active", "BUSY")
        try:
            if not self.rpc_lock.acquire(False):
                raise ControlSafetyError("a control service call is still pending", "BUSY")
            try:
                if not self._control_matches(False):
                    raise ControlSafetyError("fresh disabled control state is required",
                                             "CONTROL_STATE_UNAVAILABLE")
                with self.lock:
                    if os.path.lexists(self.path):
                        os.unlink(self.path)
                        self._sync_directory(os.path.dirname(self.path))
                    self.latched = False
            finally:
                self.rpc_lock.release()
        finally:
            self.transition_lock.release()

    def _cancel_check(self):
        self.assert_unlatched()
        if self.stop_event.is_set():
            raise ControlSafetyError("control arming was cancelled", "EXECUTION_CANCELLED")

    def _rpc(self, service_name, client, *args):
        timeout = float(self.config["control_service_timeout_seconds"])
        if not self.rpc_lock.acquire(timeout=timeout):
            raise ControlSafetyError("previous control service call is still pending", "CONTROL_SERVICE_TIMEOUT")
        done = threading.Event()
        result_lock = threading.Lock()
        result = {"abandoned": False}

        def invoke():
            try:
                try:
                    self.rospy.wait_for_service(service_name, timeout=timeout)
                    result["response"] = client(*args)
                except Exception as exc:
                    result["error"] = exc
                with result_lock:
                    abandoned = result["abandoned"]
                    if not abandoned:
                        done.set()
                # ROS service RPCs have no call timeout. A late enable must be
                # compensated before releasing serialization or permitting reset.
                if abandoned and args == (True,):
                    since = time.monotonic()
                    with self.lock:
                        revision = self.control_revision
                    response = self.enable_client(False)
                    self._require_success(response, "late enable compensation")
                    self._wait_control(False, since, revision)
            except Exception as exc:
                self.rospy.logerr("late control compensation failed: %s", exc)
            finally:
                self.rpc_lock.release()

        worker = threading.Thread(target=invoke, name="navigation-control-rpc")
        worker.daemon = True
        worker.start()
        if not done.wait(timeout):
            with result_lock:
                timed_out = not done.is_set()
                result["abandoned"] = timed_out
            if timed_out:
                self.latch("control service timeout: " + service_name)
                raise ControlSafetyError("control service timed out: " + service_name,
                                         "CONTROL_SERVICE_TIMEOUT")
        if "error" in result:
            raise ControlSafetyError("control service %s failed: %s" % (service_name, result["error"]))
        return result["response"]

    @staticmethod
    def _require_success(response, action):
        if not getattr(response, "success", False):
            raise ControlSafetyError("%s refused: %s" % (action, getattr(response, "message", "no response")),
                                     "CONTROL_SERVICE_REJECTED")

    def _wait_control(self, expected, since, revision, arming=False):
        deadline = time.monotonic() + float(self.config["control_state_timeout_seconds"])
        while time.monotonic() < deadline:
            if arming:
                self._cancel_check()
                self.assert_localized()
            if self._control_matches(expected, since, revision):
                return
            time.sleep(0.02)
        raise ControlSafetyError("fresh control enabled=%s confirmation timed out" % expected,
                                 "CONTROL_STATE_TIMEOUT")

    def arm(self):
        timeout = float(self.config["control_service_timeout_seconds"])
        if not self.transition_lock.acquire(timeout=timeout):
            raise ControlSafetyError("a control transition is pending", "CONTROL_SERVICE_TIMEOUT")
        attempted_enable = False
        try:
            self._cancel_check()
            self.assert_localized()
            if not self._control_matches(False):
                raise ControlSafetyError("fresh disabled state is required before arming",
                                         "CONTROL_STATE_UNAVAILABLE")
            response = self._rpc(self.config["navigation_reset_service"], self.reset_client)
            self._require_success(response, "navigation reset")
            self._cancel_check()
            self.assert_localized()
            since = time.monotonic()
            with self.lock:
                revision = self.control_revision
            attempted_enable = True
            response = self._rpc(self.config["control_enable_service"], self.enable_client, True)
            self._require_success(response, "control enable")
            self._wait_control(True, since, revision, arming=True)
            self._cancel_check()
        except Exception:
            if attempted_enable:
                self._disarm_locked()
            raise
        finally:
            self.transition_lock.release()

    def _disarm_locked(self):
        try:
            since = time.monotonic()
            with self.lock:
                revision = self.control_revision
            response = self._rpc(self.config["control_enable_service"], self.enable_client, False)
            self._require_success(response, "control disable")
            self._wait_control(False, since, revision)
        except Exception as exc:
            self.latch("control disable failed: %s" % exc)
            raise

    def disarm(self):
        timeout = float(self.config["control_service_timeout_seconds"])
        if not self.transition_lock.acquire(timeout=timeout):
            self.latch("control transition did not finish before disarm")
            raise ControlSafetyError("control transition did not finish before disarm", "CONTROL_SERVICE_TIMEOUT")
        try:
            self._disarm_locked()
        finally:
            self.transition_lock.release()
