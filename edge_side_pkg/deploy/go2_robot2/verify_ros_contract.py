#!/usr/bin/env python3
"""Exercise ROS service wire contracts on an owned, isolated mock master."""
import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
import xmlrpc.client


def main():
    # Both a separate master and probe-only service names prevent robot control.
    uri = "http://127.0.0.1:11321"
    if os.environ.get("ROS_MASTER_URI") != uri:
        raise SystemExit("Set ROS_MASTER_URI=" + uri + "; production masters are forbidden.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 11321))
    master = subprocess.Popen(
        ["roscore", "-p", "11321"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.monotonic() + 12
        while True:
            try:
                code, _, _ = xmlrpc.client.ServerProxy(uri).getPid("/go2_contract_probe")
                if code == 1:
                    break
            except OSError:
                pass
            if master.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Isolated ROS master failed to start")
            time.sleep(0.1)

        import rospy
        from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
        from std_msgs.msg import Bool
        from std_srvs.srv import SetBool, SetBoolResponse, Trigger, TriggerResponse
        from epgeneral_task_control.control_safety import NavigationControlSafety, ControlSafetyError

        rospy.init_node("go2_contract_probe", disable_signals=True)
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

        reset_server = rospy.Service("/ccs_probe/reset", Trigger, reset)
        enable_server = rospy.Service("/ccs_probe/enable", SetBool, enable)

        def publish(_event):
            localized.publish(Bool(True))
            message = DiagnosticArray()
            message.header.stamp = rospy.Time.now()
            message.status = [DiagnosticStatus(
                name="GO2 SDK bridge",
                values=[KeyValue("motion_enabled", str(state["enabled"]).lower())])]
            diagnostics.publish(message)

        with tempfile.TemporaryDirectory(prefix="ccs-go2-contract-") as directory:
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
            assert safety._control_matches(False)
            state["allow_reset"] = False
            try:
                safety.arm()
                raise AssertionError("A refused Trigger must not enable")
            except ControlSafetyError:
                pass
            assert state["enable_true"] == 1, state
            assert state["reset"] == 2, state
            assert state["enabled"] is False, state
            timer.shutdown()
            print(json.dumps({
                "result": "PASS", "master": uri, "namespace": "/ccs_probe",
                "reset_type": Trigger._type, "reset_md5": Trigger._md5sum,
                "enable_type": SetBool._type, "calls": state,
                "production_service_calls": 0,
            }, sort_keys=True))
        reset_server.shutdown()
        enable_server.shutdown()
        rospy.signal_shutdown("isolated contract probe finished")
    finally:
        if master.poll() is None:
            os.killpg(master.pid, signal.SIGINT)
            try:
                master.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(master.pid, signal.SIGTERM)
                master.wait(timeout=5)


if __name__ == "__main__":
    main()
