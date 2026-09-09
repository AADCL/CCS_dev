#!/usr/bin/env python3
"""Exercise actual ROS wire contracts on an owned master and mock chassis."""
import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
import xmlrpc.client


def stop_owned_master(master):
    for requested_signal, timeout in ((signal.SIGINT, 15), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        if master.poll() is not None:
            return
        try:
            os.killpg(master.pid, requested_signal)
        except ProcessLookupError:
            pass
        try:
            master.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            continue
    raise RuntimeError("Owned isolated ROS master did not terminate")


def main():
    uri = "http://127.0.0.1:11321"
    if os.environ.get("ROS_MASTER_URI") != uri:
        raise SystemExit("Set ROS_MASTER_URI=" + uri + "; other masters are forbidden.")
    # Refuse occupied ports and external remappings before creating any ROS endpoint.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 11321))
    os.environ["ROS_IP"] = "127.0.0.1"
    os.environ["ROS_NAMESPACE"] = "/ccs_probe"
    os.environ.pop("ROS_HOSTNAME", None)
    master = subprocess.Popen(
        ["roscore", "-p", "11321"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True)
    rospy = None
    timer = None
    servers = []
    report = None
    try:
        deadline = time.monotonic() + 12
        while True:
            if master.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Isolated ROS master failed to start")
            try:
                with xmlrpc.client.ServerProxy(uri) as server:
                    code, _, _ = server.getPid("/ccs_probe/go2_contract_probe")
                if code == 1:
                    break
            except OSError:
                pass
            time.sleep(0.1)

        import rospy
        from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
        from std_msgs.msg import Bool
        from std_srvs.srv import SetBool, SetBoolResponse, Trigger, TriggerResponse
        from epgeneral_task_control.control_safety import NavigationControlSafety, ControlSafetyError

        rospy.init_node("go2_contract_probe", argv=[__file__], disable_signals=True)
        if rospy.get_namespace() != "/ccs_probe/":
            raise RuntimeError("ROS probe namespace was overridden")
        state = {"enabled": False, "allow_reset": True, "reset": 0, "enable_true": 0, "enable_false": 0}
        enabled = rospy.Publisher("/ccs_probe/enabled", Bool, queue_size=10, latch=True)
        localized = rospy.Publisher("/ccs_probe/localized", Bool, queue_size=10)
        diagnostics = rospy.Publisher("/ccs_probe/diagnostics", DiagnosticArray, queue_size=10)

        def reset(_request):
            state["reset"] += 1
            return TriggerResponse(state["allow_reset"], "mock reset")

        def enable(request):
            state["enabled"] = request.data
            state["enable_true" if request.data else "enable_false"] += 1
            enabled.publish(Bool(request.data))
            return SetBoolResponse(True, "mock enable")

        servers.append(rospy.Service("/ccs_probe/reset", Trigger, reset))
        servers.append(rospy.Service("/ccs_probe/enable", SetBool, enable))

        def publish(_event):
            localized.publish(Bool(True))
            message = DiagnosticArray()
            message.header.stamp = rospy.Time.now()
            message.status = [DiagnosticStatus(
                name="GO2 SDK bridge",
                values=[KeyValue("motion_enabled", str(state["enabled"]).lower()),
                        KeyValue("low_state_age_sec", "0.01"), KeyValue("sport_state_age_sec", "0.01")])]
            diagnostics.publish(message)

        with tempfile.TemporaryDirectory(prefix="ccs-go2-robot3-contract-") as directory:
            config = {
                "navigation_reset_service": "/ccs_probe/reset",
                "control_enable_service": "/ccs_probe/enable",
                "control_enabled_topic": "/ccs_probe/enabled",
                "localization_ok_topic": "/ccs_probe/localized",
                "control_diagnostics_topic": "/ccs_probe/diagnostics",
                "emergency_stop_state_file": os.path.join(directory, "safety.json"),
                "control_service_timeout_seconds": 2.0,
                "control_state_timeout_seconds": 2.0,
                "pose_timeout_seconds": 2.0,
            }
            safety = NavigationControlSafety(rospy, config, threading.Event())
            safety.start(lambda _request: TriggerResponse(False, "probe does not reset latches"))
            enabled.publish(Bool(False))
            timer = rospy.Timer(rospy.Duration(0.05), publish)
            deadline = time.monotonic() + 5
            while not safety._control_matches(False):
                if time.monotonic() > deadline:
                    raise RuntimeError("Mock state connection timed out")
                time.sleep(0.05)
            time.sleep(0.15)
            safety.arm()
            safety.assert_running()
            safety.disarm()
            if not safety._control_matches(False):
                raise RuntimeError("Mock disarm was not confirmed")
            state["allow_reset"] = False
            try:
                safety.arm()
                raise RuntimeError("A refused Trigger must not enable")
            except ControlSafetyError:
                pass
            if state["enable_true"] != 1 or state["reset"] != 2 or state["enabled"] is not False:
                raise RuntimeError("Unexpected mock service calls: " + repr(state))
            report = {
                "result": "PASS", "master": uri, "namespace": "/ccs_probe",
                "reset_type": Trigger._type, "reset_md5": Trigger._md5sum,
                "enable_type": SetBool._type, "enable_md5": SetBool._md5sum,
                "calls": dict(state), "production_service_calls": 0,
            }
    finally:
        try:
            if timer is not None:
                timer.shutdown()
            for server in servers:
                server.shutdown()
            if rospy is not None:
                rospy.signal_shutdown("isolated contract probe finished")
        finally:
            stop_owned_master(master)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
