import hashlib
import io
import json
import math
import os
import tempfile
import zlib
from xml.etree import ElementTree


class StorageError(ValueError):
    pass


def _number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StorageError("%s must be numeric" % name)
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise StorageError("%s is out of range" % name)
    return result


def decode_trajectory(compressed, crc32, config, identity, expected_raw_bytes=None):
    if not compressed or len(compressed) > config["max_compressed_bytes"]:
        raise StorageError("compressed trajectory size is invalid")
    if zlib.crc32(compressed) & 0xFFFFFFFF != crc32:
        raise StorageError("trajectory CRC32 does not match")
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, config["max_raw_bytes"] + 1)
        raw += decoder.flush()
    except zlib.error as exc:
        raise StorageError("trajectory zlib decode failed: %s" % exc)
    if len(raw) > config["max_raw_bytes"] or not decoder.eof or decoder.unused_data:
        raise StorageError("trajectory decompressed size is invalid")
    if expected_raw_bytes is not None and len(raw) != expected_raw_bytes:
        raise StorageError("trajectory raw size does not match prepare")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StorageError("trajectory JSON decode failed: %s" % exc)
    required = ("task_id", "subtask_id", "device_id")
    if not isinstance(payload, dict) or payload.get("schema_version") not in (1, 2):
        raise StorageError("trajectory schema is invalid")
    for key in required:
        if payload.get(key) != identity[key]:
            raise StorageError("trajectory %s does not match envelope" % key)
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1 or revision != identity["revision"]:
        raise StorageError("trajectory revision is invalid")
    for key in ("task_name", "map_id", "frame_id"):
        if not isinstance(payload.get(key), str) or not payload[key] or len(payload[key]) > 256:
            raise StorageError("trajectory %s is invalid" % key)
    if payload["frame_id"] != config["map_frame"]:
        raise StorageError("trajectory frame_id does not match configured map frame")
    speed = _number(payload.get("cruise_speed_mps"), "cruise_speed_mps", 0.000001)
    delay = _number(payload.get("start_delay_seconds"), "start_delay_seconds", 0.0)
    waypoints = payload.get("waypoints")
    if not isinstance(waypoints, list) or not 2 <= len(waypoints) <= config["max_waypoints"]:
        raise StorageError("trajectory waypoint count is invalid")
    normalized = []
    ids = set()
    for index, point in enumerate(waypoints):
        if not isinstance(point, dict) or point.get("index") != index:
            raise StorageError("waypoint indices must be contiguous")
        waypoint_id = point.get("waypoint_id")
        if not isinstance(waypoint_id, str) or not waypoint_id or len(waypoint_id) > 128 or waypoint_id in ids:
            raise StorageError("waypoint ID is invalid or duplicated")
        ids.add(waypoint_id)
        normalized.append({"index": index, "waypoint_id": waypoint_id,
                           "x": _number(point.get("x"), "waypoint.x"),
                           "y": _number(point.get("y"), "waypoint.y"),
                           "z": _number(point.get("z"), "waypoint.z")})
    payload["cruise_speed_mps"] = speed
    payload["start_delay_seconds"] = delay
    payload["waypoints"] = normalized
    return payload


class TrajectoryStore(object):
    def __init__(self, root):
        self.root = os.path.abspath(root)
        if not os.path.isdir(self.root):
            os.makedirs(self.root)

    @staticmethod
    def _key(task_id, subtask_id):
        return hashlib.sha256((task_id + "\0" + subtask_id).encode("utf-8")).hexdigest()

    def paths(self, task_id, subtask_id):
        directory = os.path.join(self.root, self._key(task_id, subtask_id))
        return directory, os.path.join(directory, "trajectory.xml")

    def load(self, task_id, subtask_id):
        unused_directory, xml_path = self.paths(task_id, subtask_id)
        try:
            root = ElementTree.parse(xml_path).getroot()
            metadata_node = root.find("metadata")
            metadata = {
                "task_id": root.get("task_id"), "subtask_id": root.get("subtask_id"),
                "device_id": root.get("device_id"), "revision": int(root.get("revision")),
                "crc32": int(root.get("crc32")), "map_id": metadata_node.get("map_id"),
                "frame_id": metadata_node.get("frame_id"),
            }
        except (IOError, TypeError, ValueError, ElementTree.ParseError, AttributeError):
            return None
        if metadata.get("task_id") != task_id or metadata.get("subtask_id") != subtask_id or root.get("task_id") != task_id:
            return None
        metadata["xml_path"] = xml_path
        return metadata

    def load_payload(self, task_id, subtask_id):
        """Load the complete validated XML payload for a device adapter."""
        unused_directory, xml_path = self.paths(task_id, subtask_id)
        try:
            root = ElementTree.parse(xml_path).getroot()
            metadata_node = root.find("metadata")
            waypoints_node = root.find("waypoints")
            if metadata_node is None or waypoints_node is None:
                return None
            payload = {
                "schema_version": int(root.get("schema_version", "1")),
                "task_id": root.get("task_id"), "subtask_id": root.get("subtask_id"),
                "device_id": root.get("device_id"), "revision": int(root.get("revision")),
                "crc32": int(root.get("crc32")),
                "task_name": metadata_node.get("task_name"),
                "map_id": metadata_node.get("map_id"), "frame_id": metadata_node.get("frame_id"),
                "cruise_speed_mps": float(metadata_node.get("cruise_speed_mps")),
                "start_delay_seconds": float(metadata_node.get("start_delay_seconds")),
                "waypoints": [], "xml_path": xml_path,
            }
            for index, item in enumerate(waypoints_node.findall("waypoint")):
                if int(item.get("index")) != index:
                    return None
                payload["waypoints"].append({
                    "index": index, "waypoint_id": item.get("waypoint_id"),
                    "x": float(item.get("x")), "y": float(item.get("y")),
                    "z": float(item.get("z")),
                })
            if not payload["task_id"] == task_id or not payload["subtask_id"] == subtask_id:
                return None
            return payload
        except (IOError, TypeError, ValueError, ElementTree.ParseError, AttributeError):
            return None

    def commit(self, payload, crc32):
        directory, xml_path = self.paths(payload["task_id"], payload["subtask_id"])
        if not os.path.isdir(directory):
            os.makedirs(directory)
        root = ElementTree.Element("trajectory", {
            "schema_version": "2", "task_id": payload["task_id"], "subtask_id": payload["subtask_id"],
            "device_id": payload["device_id"], "revision": str(payload["revision"]), "crc32": str(int(crc32)),
        })
        ElementTree.SubElement(root, "metadata", {
            "task_name": payload["task_name"], "map_id": payload["map_id"], "frame_id": payload["frame_id"],
            "cruise_speed_mps": repr(payload["cruise_speed_mps"]),
            "start_delay_seconds": repr(payload["start_delay_seconds"]),
        })
        points = ElementTree.SubElement(root, "waypoints", {"count": str(len(payload["waypoints"]))})
        for point in payload["waypoints"]:
            ElementTree.SubElement(points, "waypoint", {key: str(point[key]) for key in ("index", "waypoint_id", "x", "y", "z")})
        xml_data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
        metadata = {key: payload[key] for key in ("task_id", "subtask_id", "device_id", "revision", "map_id", "frame_id")}
        metadata["crc32"] = int(crc32)
        self._atomic(xml_path, xml_data)
        metadata["xml_path"] = xml_path
        return metadata

    def delete(self, task_id, subtask_id):
        directory, xml_path = self.paths(task_id, subtask_id)
        try:
            os.unlink(xml_path)
        except OSError:
            pass
        try:
            os.rmdir(directory)
        except OSError:
            pass

    @staticmethod
    def _atomic(path, data):
        descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            try:
                directory_fd = os.open(os.path.dirname(path), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def save_execution(self, value):
        path = os.path.join(self.root, "active_execution.json")
        self._atomic(path, json.dumps(value, sort_keys=True).encode("utf-8"))

    def load_execution(self):
        path = os.path.join(self.root, "active_execution.json")
        try:
            with io.open(path, "r", encoding="utf-8") as stream:
                return json.load(stream)
        except (IOError, ValueError):
            return None

    def clear_execution(self):
        try:
            os.unlink(os.path.join(self.root, "active_execution.json"))
        except OSError:
            pass
