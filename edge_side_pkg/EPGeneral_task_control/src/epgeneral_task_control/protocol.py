import math
import time

import msgpack


COMMAND_TYPES = {"task_prepare", "task_chunk", "task_commit", "execute_task", "cancel_execution", "stop_task"}
MESSAGE_TYPES = COMMAND_TYPES | {"command_ack", "task_heartbeat", "task_status", "waypoint_progress"}


class ProtocolError(ValueError):
    pass


def _identifier(value, name, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value) or len(value) > 128:
        raise ProtocolError("%s is invalid" % name)
    return value


def _integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError("%s is invalid" % name)
    return value


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError("payload contains a non-finite number")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


def decode(datagram, config):
    if len(datagram) > config["max_datagram_bytes"]:
        raise ProtocolError("datagram exceeds configured limit")
    try:
        try:
            raw = msgpack.unpackb(datagram, raw=False, strict_map_key=True)
        except TypeError:
            raw = msgpack.unpackb(datagram, raw=False)
    except Exception as exc:
        raise ProtocolError("MessagePack decode failed: %s" % exc)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ProtocolError("schema_version is unsupported")
    if raw.get("protocol_id") != config["protocol_id"]:
        raise ProtocolError("protocol_id does not match")
    for key in ("task_id", "subtask_id", "device_id", "request_id"):
        _identifier(raw.get(key), key)
    _identifier(raw.get("execution_id", ""), "execution_id", True)
    if raw.get("message_type") not in COMMAND_TYPES:
        raise ProtocolError("message_type is not a command")
    _integer(raw.get("sequence"), "sequence")
    _integer(raw.get("sent_at_ns"), "sent_at_ns")
    if not isinstance(raw.get("payload"), dict):
        raise ProtocolError("payload must be a mapping")
    _finite(raw["payload"])
    return raw


def encode(config, identity, message_type, sequence, payload, request_id=None):
    if message_type not in MESSAGE_TYPES:
        raise ProtocolError("message_type is invalid")
    _finite(payload)
    raw = {
        "schema_version": 1, "protocol_id": config["protocol_id"],
        "task_id": identity["task_id"], "subtask_id": identity["subtask_id"],
        "device_id": config["device_id"], "execution_id": identity.get("execution_id", ""),
        "message_type": message_type, "request_id": request_id or identity.get("request_id", "status"),
        "sequence": int(sequence), "sent_at_ns": int(time.time() * 1000000000), "payload": payload,
    }
    encoded = msgpack.packb(raw, use_bin_type=True)
    if len(encoded) > config["max_datagram_bytes"]:
        raise ProtocolError("encoded datagram exceeds configured limit")
    return encoded


def signature(command):
    canonical = msgpack.packb(command, use_bin_type=True)
    import hashlib
    return hashlib.sha256(canonical).hexdigest()
