#!/usr/bin/env python3
"""Short-lived CCS client for the boot-owned mapping stage service."""
import sys
import time

SERVICE = "/ground_air/system/set_stage"
GUARD_PARAM = "/ground_air_stage_manager/ccs_session_guard_version"
SUPPORTED_GUARD_VERSIONS = (1, 2)
EXTERNAL_TF_PARAM = "/ground_air_stage_manager/external_tf_required"
LEGACY_RESIDENT_TF_PARAM = "/ground_air_stage_manager/resident_tf_version"
STATIC_TF_NODES = {
    "/odom_camera_init_broadcaster",
    "/base_link_body_broadcaster",
}


def request_stage(call, stage, map_id, timeout):
    response = call(stage=stage, map_id=map_id, timeout=timeout)
    if not response.success or response.active_stage != stage:
        raise RuntimeError("set_stage rejected: {} (active_stage={})".format(
            response.message, response.active_stage))
    return response


def wait_nodes(get_nodes, expected, timeout, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while clock() < deadline:
        if set(expected).issubset(set(get_nodes())):
            return
        sleep(0.2)
    raise RuntimeError("mapping nodes did not become ready: {}".format(",".join(expected)))


def require_external_tf(get_param, get_nodes):
    if get_param(LEGACY_RESIDENT_TF_PARAM, 0) == 1:
        raise RuntimeError("legacy resident TF ownership is still enabled")
    if get_param(EXTERNAL_TF_PARAM, 0) != 1:
        raise RuntimeError("stage manager is not configured for external TF")
    missing = STATIC_TF_NODES.difference(get_nodes())
    if missing:
        raise RuntimeError(
            "startup coordinate transforms are not ready: {}".format(
                ",".join(sorted(missing))))


def action_requires_external_tf(mode):
    return mode in ("--check", "--start")


def main(args):
    import re
    import rospy
    import rosnode
    from ground_air_msgs.srv import SetSystemStage

    mode = args[0]
    if mode not in ("--check", "--start", "--stop", "--abort"):
        raise ValueError("unsupported action")
    if mode == "--check":
        owner = "preflight"
    else:
        owner = args[1]
        if not re.fullmatch(r"[a-f0-9]{32}", owner):
            raise ValueError("invalid CCS session identity")
    rospy.init_node("ccs_mapping_stage_" + owner, disable_signals=True)
    rospy.wait_for_service(SERVICE, timeout=4.0)
    guard_version = rospy.get_param(GUARD_PARAM, None)
    if (type(guard_version) is not int
            or guard_version not in SUPPORTED_GUARD_VERSIONS):
        raise RuntimeError(
            "unsupported CCS session ownership guard version: "
            "actual={!r}; supported={}".format(
                guard_version, SUPPORTED_GUARD_VERSIONS))
    if action_requires_external_tf(mode):
        require_external_tf(rospy.get_param, rosnode.get_node_names)
    if mode == "--check":
        print("ground-air stage service ready; session guard enabled")
        return

    map_id, timeout = args[2], float(args[3])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", map_id):
        raise ValueError("invalid map_id")
    if timeout <= 0:
        raise ValueError("invalid timeout")
    proxy = rospy.ServiceProxy(SERVICE, SetSystemStage)
    target = 1 if mode == "--start" else 0
    started = time.monotonic()
    response = request_stage(proxy, target, map_id, timeout)
    if target == 1:
        wait_nodes(rosnode.get_node_names, args[4].split(","),
                   max(0.1, timeout - (time.monotonic() - started)))
    print("stage=ground_air_mapping action={} map_id={} active_stage={} message={}".format(
        mode[2:], map_id, response.active_stage, response.message))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as error:
        print("ground_air_stage_client: {}".format(error), file=sys.stderr)
        sys.exit(1)
