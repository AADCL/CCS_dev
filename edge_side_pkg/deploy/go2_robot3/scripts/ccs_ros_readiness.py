#!/usr/bin/env python3
"""Observe fresh ROS data and chassis diagnostics without sending controls."""
import argparse
import math
import threading
import time


def disabled_diagnostics(message, now, started, max_age):
    stamp = message.header.stamp.to_sec()
    if stamp < started or not 0 <= now - stamp <= max_age:
        return False
    for status in message.status:
        if status.name != "GO2 SDK bridge":
            continue
        values = {item.key: item.value for item in status.values}
        if values.get("motion_enabled", "").lower() != "false":
            return False
        try:
            ages = [float(values[key]) for key in ("low_state_age_sec", "sport_state_age_sec")]
        except (KeyError, ValueError):
            return False
        return all(math.isfinite(age) and 0 <= age <= max_age for age in ages)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inputs", "disabled"))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-age", type=float, default=3.0)
    args = parser.parse_args()
    import rospy
    import roslib.message

    rospy.init_node("ccs_go2_readiness", anonymous=True, disable_signals=True)
    started = time.time()
    lock = threading.Lock()
    observations = {}
    enabled = [None]
    if args.mode == "inputs":
        topics = {
            "/livox/lidar": "livox_ros_driver2/CustomMsg",
            "/livox/imu": "sensor_msgs/Imu",
            "/go2/imu": "sensor_msgs/Imu",
            "/go2/state/low_state": "go2_control/Go2LowState",
            "/go2/battery_state": "sensor_msgs/BatteryState",
            "/camera/color/image_raw": "sensor_msgs/Image",
        }
    else:
        topics = {"/go2/diagnostics": "diagnostic_msgs/DiagnosticArray"}

    def observe(message, topic):
        now = time.time()
        stamp = getattr(getattr(message, "header", None), "stamp", None)
        stamp = stamp.to_sec() if stamp is not None else now
        valid = stamp >= started and 0 <= now - stamp <= args.max_age
        if args.mode == "disabled":
            valid = disabled_diagnostics(message, now, started, args.max_age)
        with lock:
            count, previous, _ = observations.get(topic, (0, 0.0, 0.0))
            observations[topic] = ((count + 1) if valid and stamp > previous else 0, stamp, now)

    subscribers = [rospy.Subscriber(topic, roslib.message.get_message_class(type_name),
                                   observe, callback_args=topic, queue_size=1)
                   for topic, type_name in topics.items()]
    if args.mode == "disabled":
        from std_msgs.msg import Bool
        def observe_enabled(message):
            with lock:
                enabled[0] = message.data
        subscribers.append(rospy.Subscriber("/go2/control/enabled", Bool, observe_enabled, queue_size=1))
    deadline = time.monotonic() + args.timeout
    pending = list(topics)
    try:
        while time.monotonic() < deadline and not rospy.is_shutdown():
            now = time.time()
            with lock:
                pending = [topic for topic in topics if observations.get(topic, (0, 0, 0))[0] < 2
                           or now - observations[topic][2] > args.max_age]
                if args.mode == "disabled" and enabled[0] is not False:
                    pending.append("/go2/control/enabled=false")
            if not pending:
                print("Fresh {} confirmed: {}".format(args.mode, ", ".join(topics)))
                return 0
            time.sleep(0.05)
        print("Fresh {} not confirmed: {}".format(args.mode, ", ".join(pending)))
        return 1
    finally:
        for subscriber in subscribers:
            subscriber.unregister()
        rospy.signal_shutdown("readiness observation completed")


if __name__ == "__main__":
    raise SystemExit(main())
