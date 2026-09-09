#!/usr/bin/env python3

import importlib.util
import os
import tempfile
import unittest


SCRIPT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "scripts", "go2_stack_guard.py"))
SPEC = importlib.util.spec_from_file_location("go2_stack_guard", SCRIPT_PATH)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class ConflictDetectionTest(unittest.TestCase):
    def test_owned_sibling_from_same_roslaunch_is_allowed(self):
        conflicts = GUARD.find_conflicts(
            ["/laserMapping"], ["/laserMapping"], 100,
            parent_lookup=lambda _name: 100)
        self.assertEqual([], conflicts)

    def test_preexisting_node_with_different_parent_is_rejected(self):
        conflicts = GUARD.find_conflicts(
            ["/laserMapping"], ["/laserMapping"], 100,
            parent_lookup=lambda _name: 99)
        self.assertEqual(["/laserMapping"], conflicts)

    def test_parent_lookup_failure_is_rejected(self):
        def unavailable(_name):
            raise OSError("node exited")

        conflicts = GUARD.find_conflicts(
            ["/laserMapping"], ["/laserMapping"], 100,
            parent_lookup=unavailable)
        self.assertEqual(["/laserMapping"], conflicts)

    def test_non_owned_conflict_is_rejected_even_with_same_parent(self):
        conflicts = GUARD.find_conflicts(
            ["/move_base"], ["/laserMapping"], 100,
            parent_lookup=lambda _name: 100)
        self.assertEqual(["/move_base"], conflicts)


class ProcessParentTest(unittest.TestCase):
    def test_reads_ppid_when_process_name_contains_spaces(self):
        with tempfile.TemporaryDirectory() as proc_root:
            process_dir = os.path.join(proc_root, "321")
            os.mkdir(process_dir)
            with open(os.path.join(process_dir, "stat"), "w", encoding="utf-8") as stream:
                stream.write("321 (name with spaces) S 123 1 2 3\n")
            self.assertEqual(123, GUARD.process_parent_pid(321, proc_root))


if __name__ == "__main__":
    unittest.main()
