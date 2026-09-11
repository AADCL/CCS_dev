"""Execute GO2_3 watchdog functions with fake ROS queries and owned processes."""
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time
import unittest

STARTUP = Path(os.environ.get("GO2_ROBOT3_STARTUP", str(
    Path(__file__).resolve().parents[1] /
    "edge_side_pkg/deploy/go2_robot3/start_ccs_edge_dev.sh")))
BASH = os.environ.get("CCS_TEST_BASH") or (shutil.which("bash") if os.name != "nt" else None)


@unittest.skipUnless(BASH, "native Bash or CCS_TEST_BASH is required")
class Go2WatchdogTests(unittest.TestCase):
    def run_check(self, scenario="healthy", invocation="check_runtime_nodes", alive=True):
        source = STARTUP.read_text(encoding="utf-8")
        functions = source[source.index("ros_node_exists() {"):source.index("master_exists() {")]
        shell = r"""
set -eo pipefail
LOG_DIR=.
STATE_DIR=.
NODE_NAMES=(/go2_sdk_bridge_real /other)
LAUNCH_NAMES=(control other)
PIDS=(111 222)
ROSCORE_MANAGED=false
ROSCORE_PID=""
report() { printf '[%s] %s\n' "$1" "$2"; }
fail() { report ERROR "$*" >&2; exit 1; }
owned_process_alive() { ALIVE; }
sleep() { :; }
timeout() { shift; "$@"; }
rosnode() {
  local calls=0
  [[ ! -f calls ]] || read -r calls < calls
  calls=$((calls + 1))
  printf '%s\n' "$calls" > calls
  if [[ "$scenario" == timeout ]] || [[ "$scenario" == transient && "$calls" == 1 ]]; then
    printf 'simulated master timeout\n' >&2
    return 124
  fi
  if [[ "$scenario" == missing ]]; then
    printf '/other\n/epgeneral_navigation_task_adapter\n/go2_velocity_shaper\n'
    return 0
  fi
  printf '/go2_sdk_bridge_real\n/other\n/epgeneral_navigation_task_adapter\n/go2_velocity_shaper\n'
  if [[ "$scenario" == partial ]]; then
    printf 'simulated truncated query\n' >&2
    return 1
  fi
  if [[ "$scenario" == large ]]; then
    for ((i=0; i<10000; i++)); do
      printf '/mapping_node_%05d_with_extra_output\n' "$i"
    done
  fi
  return 0
}
"""
        shell = shell.replace("ALIVE", "return 0" if alive else "return 1")
        shell += "\nscenario=" + scenario + "\n" + functions + "\n" + invocation + "\n"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([BASH, "-s"], input=shell, cwd=directory,
                                    capture_output=True, text=True, timeout=15)
            root = Path(directory)
            calls = int((root / "calls").read_text()) if (root / "calls").exists() else 0
            log = (root / "runtime_monitor.log").read_text() if (root / "runtime_monitor.log").exists() else ""
        return result, calls, log

    def test_healthy_snapshot_queries_once_and_stays_quiet(self):
        result, calls, log = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 1)
        self.assertEqual(result.stdout + result.stderr + log, "")

    def test_transient_query_failure_recovers_without_shutdown(self):
        result, calls, log = self.run_check("transient")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 2)
        self.assertIn("simulated master timeout", log)
        self.assertIn("recovered on attempt 2", log)
        self.assertEqual(result.stdout + result.stderr, "")

    def test_persistent_query_failure_is_not_reported_as_bridge_exit(self):
        result, calls, log = self.run_check("timeout")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 3)
        self.assertIn("ROS node query failed (exit 124) after 3 checks", result.stderr)
        self.assertIn("runtime_monitor.log", result.stderr)
        self.assertNotIn("/go2_sdk_bridge_real exited", result.stderr)
        self.assertEqual(log.count("simulated master timeout"), 3)

    def test_partial_failed_query_cannot_count_as_healthy(self):
        result, calls, log = self.run_check("partial")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 3)
        self.assertIn("query failed (exit 1)", result.stderr)
        self.assertIn("truncated query", log)

    def test_missing_bridge_still_stops_workflow(self):
        result, calls, log = self.run_check("missing")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 3)
        self.assertIn("Required ROS node /go2_sdk_bridge_real is absent", result.stderr)
        self.assertIn("control.log", result.stderr)

    def test_dead_owned_launch_is_immediate_even_if_master_would_answer(self):
        result, calls, _ = self.run_check(alive=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 0)
        self.assertIn("control roslaunch exited or ownership changed", result.stderr)

    def test_dead_owned_master_is_immediate(self):
        result, calls, _ = self.run_check(invocation=(
            "ROSCORE_MANAGED=true\nROSCORE_PID=333\n"
            'owned_process_alive() { [[ "$1" != 333 ]]; }\ncheck_runtime_nodes'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 0)
        self.assertIn("Owned ROS master exited", result.stderr)

    def test_node_lookup_consumes_large_snapshot_under_pipefail(self):
        result, calls, _ = self.run_check("large", "ros_node_exists /go2_sdk_bridge_real")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, 1)

    def test_node_lookup_rejects_failed_partial_snapshot(self):
        result, calls, _ = self.run_check("partial", "ros_node_exists /go2_sdk_bridge_real")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, 1)

    @unittest.skipUnless(os.name == "posix" and shutil.which("setsid"),
                         "Linux setsid is required")
    def test_detached_child_survives_terminal_signal_until_ordered_cleanup(self):
        source = r'''#!/usr/bin/env bash
set -e
parent_marker="$1"
child_marker="$2"
child_pid_file="$3"
setsid bash -c '
  trap "printf INT >\"$1\"; exit 2" INT
  trap "printf TERM >\"$1\"; exit 0" TERM
  while true; do sleep 0.1; done
' _ "${child_marker}" &
child=$!
printf '%s' "${child}" >"${child_pid_file}"
ordered_cleanup() {
  printf '%s' "$1" >"${parent_marker}"
  kill -TERM "${child}"
  wait "${child}"
  exit 0
}
trap 'ordered_cleanup INT' INT
trap 'ordered_cleanup TERM' TERM
while true; do sleep 0.1; done
'''
        for sent_signal, expected in ((signal.SIGINT, "INT"), (signal.SIGTERM, "TERM")):
            with self.subTest(signal=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                helper = root / "session-probe.sh"
                parent_marker = root / "parent"
                child_marker = root / "child"
                child_pid_file = root / "child.pid"
                with helper.open("w", encoding="utf-8", newline="\n") as stream:
                    stream.write(source)
                probe = subprocess.Popen(
                    [BASH, str(helper), str(parent_marker), str(child_marker), str(child_pid_file)],
                    start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 5.0
                    while not child_pid_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(child_pid_file.exists())
                    child_pid = int(child_pid_file.read_text())
                    self.assertEqual(os.getpgid(child_pid), child_pid)
                    os.killpg(probe.pid, sent_signal)
                    self.assertEqual(probe.wait(timeout=10), 0)
                    self.assertEqual(parent_marker.read_text(), expected)
                    self.assertEqual(child_marker.read_text(), "TERM")
                finally:
                    if probe.poll() is None:
                        os.killpg(probe.pid, signal.SIGTERM)
                        probe.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
