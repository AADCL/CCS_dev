#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if not SCRIPTS.is_dir():
    SCRIPTS = ROOT / "car_bringup_scripts"
CORE_MODULE = SCRIPTS / "system_stage_core.py"
RUNTIME_MODULE = SCRIPTS / "system_stage_runtime.py"


def load_module(name, path):
    if not path.is_file():
        raise AssertionError("{} is missing".format(path.name))
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBackend:
    def __init__(self):
        self.events = []
        self.conflicts = []
        self.primary_ready = True
        self.transforms_ready = True
        self.next_handle = 0

    def find_conflicts(self, managed_stage_active):
        self.events.append(("conflicts", managed_stage_active))
        return list(self.conflicts)

    def start(self, command):
        self.next_handle += 1
        handle = "process-{}".format(self.next_handle)
        self.events.append(("start", tuple(command), handle))
        return handle

    def wait_primary(self, stage, handle, timeout):
        self.events.append(("wait_primary", stage, handle, timeout))
        return self.primary_ready

    def wait_stage_transforms(self, stage, handle, timeout):
        self.events.append(("wait_stage_transforms", stage, handle, timeout))
        return self.transforms_ready

    def stop(self, handle):
        self.events.append(("stop", handle))


class GroundAirStageManagerTests(unittest.TestCase):
    def setUp(self):
        self.core = load_module("system_stage_core", CORE_MODULE)
        self.runtime = load_module("system_stage_runtime", RUNTIME_MODULE)
        self.backend = FakeBackend()
        self.manager = self.runtime.StageController(self.backend)

    def transition(self, stage, map_id="", timeout=30.0):
        request = self.core.normalize_request(stage, map_id, timeout)
        return self.manager.transition(request, self.core.build_stage_commands(request))

    def test_mapping_starts_only_primary_and_checks_resident_transforms(self):
        result = self.transition(self.core.MAPPING, "site_a")
        self.assertTrue(result.success)
        starts = [event[1] for event in self.backend.events if event[0] == "start"]
        self.assertIn("manual_mapping_control.launch", starts[0])
        self.assertEqual(1, len(starts))
        self.assertTrue(any(
            event[0] == "wait_stage_transforms"
            for event in self.backend.events))
        self.assertEqual(self.core.MAPPING, self.manager.active_stage)

    def test_same_stage_and_map_is_idempotent(self):
        self.transition(self.core.MAPPING, "site_a")
        event_count = len(self.backend.events)
        result = self.transition(self.core.MAPPING, "site_a")
        self.assertTrue(result.success)
        self.assertEqual(event_count, len(self.backend.events))

    def test_conflict_rejection_preserves_current_stage(self):
        self.transition(self.core.MAPPING, "site_a")
        self.backend.conflicts = ["/foreign_fast_lio"]
        result = self.transition(self.core.RELOCALIZATION, "site_a")
        self.assertFalse(result.success)
        self.assertEqual(self.core.MAPPING, self.manager.active_stage)
        self.assertFalse(any(event[0] == "stop" for event in self.backend.events[-2:]))

    def test_switch_stops_old_children_before_starting_new_stage(self):
        self.transition(self.core.MAPPING, "site_a")
        marker = len(self.backend.events)
        result = self.transition(self.core.RELOCALIZATION, "site_a")
        self.assertTrue(result.success)
        transition_events = self.backend.events[marker:]
        stop_indexes = [i for i, event in enumerate(transition_events) if event[0] == "stop"]
        start_indexes = [i for i, event in enumerate(transition_events) if event[0] == "start"]
        self.assertLess(max(stop_indexes), min(start_indexes))

    def test_failed_transform_readiness_rolls_back_to_base(self):
        self.backend.transforms_ready = False
        result = self.transition(self.core.MAPPING, "site_a")
        self.assertFalse(result.success)
        self.assertEqual(self.core.BASE, self.manager.active_stage)
        self.assertEqual([], self.manager.children)
        self.assertEqual(1, len([e for e in self.backend.events if e[0] == "stop"]))

    def test_base_stops_only_managed_children(self):
        self.transition(self.core.RELOCALIZATION, "site_a")
        result = self.transition(self.core.BASE)
        self.assertTrue(result.success)
        self.assertEqual(self.core.BASE, self.manager.active_stage)
        self.assertEqual([], self.manager.children)


if __name__ == "__main__":
    unittest.main()
