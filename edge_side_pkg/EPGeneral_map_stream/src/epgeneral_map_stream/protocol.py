import ipaddress
import math
import time
import zlib

import msgpack


MESSAGE_TYPES = {
    "prepare_mapping", "prepare_result", "start_mapping", "stop_mapping", "abort_mapping",
    "cloud_fragment_ack", "request_cloud_chunks", "cloud_chunk",
    "command_ack", "session_heartbeat", "session_status", "cloud_fragment_ready",
    "artifact_status",
}


class ProtocolError(ValueError):
    pass


def now_ns():
    return int(time.time() * 1000000000)


def _identifier(value, name, maximum=128):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ProtocolError("%s is invalid" % name)
    return value


def _integer(value, name, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError("%s is invalid" % name)
    if maximum is not None and value > maximum:
        raise ProtocolError("%s is out of range" % name)
    return value


def _number(value, name, minimum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError("%s is invalid" % name)
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ProtocolError("%s is out of range" % name)
    return number


def unpack_envelope(datagram, config):
    if len(datagram) > config["max_datagram_bytes"]:
        raise ProtocolError("datagram exceeds configured limit")
    try:
        try:
            raw = msgpack.unpackb(datagram, raw=False, strict_map_key=True)
        except TypeError:
            raw = msgpack.unpackb(datagram, raw=False)
    except Exception as exc:
        raise ProtocolError("MessagePack decode failed: %s" % exc)
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise ProtocolError("schema_version is unsupported")
    if raw.get("protocol_id") != config["protocol_id"]:
        raise ProtocolError("protocol_id does not match")
    for key in ("map_id", "device_id", "session_id"):
        _identifier(raw.get(key), key)
    if raw.get("message_type") not in MESSAGE_TYPES:
        raise ProtocolError("message_type is unsupported")
    _integer(raw.get("sequence"), "sequence")
    _integer(raw.get("sent_at_ns"), "sent_at_ns")
    if not isinstance(raw.get("payload"), dict):
        raise ProtocolError("payload must be a mapping")
    return raw


def decode_command(datagram, config):
    raw = unpack_envelope(datagram, config)
    message_type = raw["message_type"]
    if message_type not in (
            "prepare_mapping", "start_mapping", "stop_mapping", "abort_mapping",
            "cloud_fragment_ack", "request_cloud_chunks"):
        raise ProtocolError("only mapping commands are accepted")
    payload = raw["payload"]
    _identifier(payload.get("request_id"), "request_id", 256)
    joint = ["job_id", "role", "primary_device_id"]
    present = [name in payload for name in joint]
    if any(present) and not all(present):
        raise ProtocolError("joint mapping fields must be provided together")
    if all(present):
        _identifier(payload["job_id"], "job_id")
        _identifier(payload["primary_device_id"], "primary_device_id")
        if payload["role"] not in ("primary", "secondary"):
            raise ProtocolError("role is invalid")
        if ((payload["role"] == "primary")
                != (raw["device_id"].casefold()
                    == payload["primary_device_id"].casefold())):
            raise ProtocolError("role does not match primary_device_id")
    if message_type == "prepare_mapping":
        try:
            ipaddress.ip_address(str(payload.get("return_host")))
        except ValueError as exc:
            raise ProtocolError("return_host is invalid") from exc
        _integer(payload.get("return_port"), "return_port", 1, 65535)
        required = payload.get("required_inputs")
        if (not isinstance(required, list) or not required or len(required) > 64
                or any(not isinstance(item, str) or not item.strip() for item in required)):
            raise ProtocolError("required_inputs is invalid")
        if ("restart_active" in payload
                and not isinstance(payload["restart_active"], bool)):
            raise ProtocolError("restart_active is invalid")
        if payload.get("preview_transport", "pcd_fragment_http") != "pcd_fragment_http":
            raise ProtocolError("unsupported preview transport")
        _number(payload.get("fragment_interval_seconds", 1.0), "fragment_interval_seconds", 0.1)
    elif message_type == "start_mapping":
        if payload.get("coordinate_contract") != "sensor+map_body+body_sensor":
            raise ProtocolError("unsupported coordinate contract")
        if payload.get("preview_transport", "pcd_fragment_http") != "pcd_fragment_http":
            raise ProtocolError("unsupported preview transport")
        _number(payload.get("fragment_interval_seconds", 1.0), "fragment_interval_seconds", 0.1)
    elif message_type in ("stop_mapping", "abort_mapping"):
        _identifier(payload.get("reason"), "reason", 256)
    elif message_type == "cloud_fragment_ack":
        _integer(payload.get("fragment_id"), "fragment_id")
    else:
        _integer(payload.get("frame_id"), "frame_id")
        missing = payload.get("missing_chunks")
        if (not isinstance(missing, list) or not missing
                or any(not isinstance(item, int) or item < 0 for item in missing)):
            raise ProtocolError("missing_chunks is invalid")
    return raw


def encode_envelope(config, identity, message_type, sequence, payload):
    if message_type not in MESSAGE_TYPES:
        raise ProtocolError("message_type is invalid")
    raw = {
        "schema_version": 2,
        "protocol_id": config["protocol_id"],
        "map_id": identity["map_id"],
        "device_id": config["device_id"],
        "session_id": identity["session_id"],
        "message_type": message_type,
        "sequence": int(sequence),
        "sent_at_ns": now_ns(),
        "payload": payload,
    }
    encoded = msgpack.packb(raw, use_bin_type=True)
    if len(encoded) > config["max_datagram_bytes"]:
        raise ProtocolError("datagram exceeds configured limit")
    return encoded


def encode_cloud_chunks(config, identity, first_sequence, metadata, compressed):
    """Legacy encoder retained for v0.5 test/tools; v0.6 runtime does not call it."""
    crc32 = zlib.crc32(compressed) & 0xFFFFFFFF
    chunk_size = max(1, config["max_datagram_bytes"] - 512)
    while chunk_size > 0:
        pieces = [compressed[index:index + chunk_size]
                  for index in range(0, len(compressed), chunk_size)]
        encoded = []
        try:
            for index, piece in enumerate(pieces):
                payload = dict(metadata)
                payload.update({"chunk_count": len(pieces), "chunk_index": index,
                                "frame_crc32": crc32, "data": piece})
                encoded.append(encode_envelope(
                    config, identity, "cloud_chunk", first_sequence + index, payload))
            return encoded
        except ProtocolError:
            chunk_size -= 32
    raise ProtocolError("cannot fit legacy cloud chunks")
