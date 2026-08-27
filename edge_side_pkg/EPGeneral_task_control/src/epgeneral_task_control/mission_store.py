"""Persistent v2 mission storage.

The store deliberately has no ROS dependency so it can be exercised on the
ground station and on an edge device with the same validation rules.
"""
from __future__ import absolute_import

import datetime
import json
import os
import re
import tempfile


STATES = frozenset(("no_task", "task_exists", "receiving", "received", "ready",
                    "running", "completed", "failed", "emergency_stop"))


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _safe(value):
    value = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value)).strip('._')
    if not value:
        raise ValueError("mission identifier is empty")
    return value[:128]


class MissionStore(object):
    def __init__(self, root):
        self.root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(self.root):
            os.makedirs(self.root)

    def directory(self, task_id):
        return os.path.join(self.root, _safe(task_id))

    def manifest_path(self, task_id):
        return os.path.join(self.directory(task_id), "manifest.json")

    def subtask_path(self, task_id, device_id, timestamp=None):
        stamp = timestamp or _utc_now().strftime("%Y%m%dT%H%M%SZ")
        return os.path.join(self.directory(task_id), "%s_%s.json" % (stamp, _safe(device_id)))

    def save(self, payload, state="ready"):
        task_id = payload.get("task_id")
        device_id = payload.get("device_id")
        if not task_id or not device_id or state not in STATES:
            raise ValueError("invalid mission identity or state")
        directory = self.directory(task_id)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        record = dict(payload)
        record["state"] = state
        record["saved_at"] = _utc_now().isoformat() + "Z"
        self._atomic_json(self.subtask_path(task_id, device_id), record)
        manifest = self.load_manifest(task_id) or {
            "schema_version": 2, "task_id": task_id, "state": "task_exists", "subtasks": {}
        }
        manifest["subtasks"][device_id] = {
            "subtask_id": payload.get("subtask_id", ""),
            "revision": payload.get("revision", 0), "state": state,
        }
        manifest["state"] = state
        self._atomic_json(self.manifest_path(task_id), manifest)
        return record

    def load_manifest(self, task_id):
        try:
            with open(self.manifest_path(task_id), "r") as stream:
                value = json.load(stream)
            return value if isinstance(value, dict) else None
        except (IOError, ValueError):
            return None

    def load(self, task_id, device_id):
        directory = self.directory(task_id)
        candidates = []
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                if name.endswith("_%s.json" % _safe(device_id)):
                    candidates.append(os.path.join(directory, name))
        if not candidates:
            return None
        candidates.sort(key=lambda value: os.path.getmtime(value), reverse=True)
        try:
            with open(candidates[0], "r") as stream:
                return json.load(stream)
        except (IOError, ValueError):
            return None

    def status(self, task_id, device_id):
        record = self.load(task_id, device_id)
        if record is None:
            return {"state": "no_task", "revision": None}
        return {"state": record.get("state", "task_exists"), "revision": record.get("revision")}

    def latest(self, device_id):
        records = []
        suffix = "_%s.json" % _safe(device_id)
        if os.path.isdir(self.root):
            for task_name in os.listdir(self.root):
                directory = os.path.join(self.root, task_name)
                if not os.path.isdir(directory):
                    continue
                for name in os.listdir(directory):
                    path = os.path.join(directory, name)
                    if name.endswith(suffix) and os.path.isfile(path):
                        records.append(path)
        records.sort(key=os.path.getmtime, reverse=True)
        for path in records:
            try:
                with open(path, "r") as stream:
                    value = json.load(stream)
                if isinstance(value, dict):
                    return value
            except (IOError, OSError, ValueError):
                continue
        return None

    def update_state(self, task_id, device_id, state):
        if state not in STATES:
            raise ValueError("invalid mission state")
        record = self.load(task_id, device_id)
        if record is None:
            return None
        record["state"] = state
        record["updated_at"] = _utc_now().isoformat() + "Z"
        directory = self.directory(task_id)
        suffix = "_%s.json" % _safe(device_id)
        candidates = [os.path.join(directory, name) for name in os.listdir(directory)
                      if name.endswith(suffix) and os.path.isfile(os.path.join(directory, name))]
        candidates.sort(key=os.path.getmtime, reverse=True)
        if not candidates:
            return None
        self._atomic_json(candidates[0], record)
        manifest = self.load_manifest(task_id)
        if manifest is not None:
            subtask = manifest.get("subtasks", {}).get(device_id)
            if isinstance(subtask, dict):
                subtask["state"] = state
            manifest["state"] = state
            self._atomic_json(self.manifest_path(task_id), manifest)
        return record

    def delete(self, task_id, device_id=None):
        directory = self.directory(task_id)
        if device_id is None:
            if os.path.isdir(directory):
                for name in os.listdir(directory):
                    os.unlink(os.path.join(directory, name))
                os.rmdir(directory)
            return
        suffix = "_%s.json" % _safe(device_id)
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                if name.endswith(suffix):
                    os.unlink(os.path.join(directory, name))
        manifest = self.load_manifest(task_id)
        if manifest:
            manifest.get("subtasks", {}).pop(device_id, None)
            manifest["state"] = "task_exists" if manifest.get("subtasks") else "no_task"
            self._atomic_json(self.manifest_path(task_id), manifest)

    @staticmethod
    def _atomic_json(path, value):
        descriptor, temporary = tempfile.mkstemp(prefix=".mission-", dir=os.path.dirname(path))
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(value, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
