import ipaddress
import math
import time
import zlib

import msgpack


MESSAGE_TYPES = {
    "start_mapping", "stop_mapping", "command_ack", "session_heartbeat",
    "cloud_chunk", "session_status",
}


class ProtocolError(ValueError):
    def __init__(self, message, command=None):
        super().__init__(message)
        self.command = command


def now_ns():
    return time.time_ns()


def _identifier(value, name, maximum=128):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ProtocolError("%s is invalid" % name)
    return value.strip()


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


def _unpack(datagram):
    try:
        try:
            return msgpack.unpackb(datagram, raw=False, strict_map_key=True)
        except TypeError:
            return msgpack.unpackb(datagram, raw=False)
    except Exception as exc:
        raise ProtocolError("MessagePack decode failed: %s" % exc)


def _validate_start(raw, config):
    payload = raw["payload"]
    _identifier(payload.get("request_id"), "request_id", 256)
    try:
        ipaddress.ip_address(str(payload.get("return_host")))
    except ValueError as exc:
        raise ProtocolError("return_host is invalid") from exc
    _integer(payload.get("return_port"), "return_port", 1, 65535)
    _number(payload.get("cloud_rate_hz"), "cloud_rate_hz", 0.001)
    _number(payload.get("voxel_size_m"), "voxel_size_m", 0.001)
    if payload.get("compression") != "zlib" or payload.get("point_format") != "xyz_f32_le":
        raise ProtocolError("unsupported compression or point format")
    if payload.get("coordinate_contract") != "sensor+map_body+body_sensor":
        raise ProtocolError("unsupported coordinate contract")

    _identifier(payload.get("job_id"), "job_id")
    role = payload.get("role")
    if role not in ("primary", "secondary"):
        raise ProtocolError("role is invalid")
    primary = _identifier(payload.get("primary_device_id"), "primary_device_id")
    participants = payload.get("participant_device_ids")
    if not isinstance(participants, list):
        raise ProtocolError("participant_device_ids must be a list")
    normalized = [
        _identifier(item, "participant_device_ids").casefold() for item in participants
    ]
    if len(normalized) < 2 or len(normalized) > config["max_participant_devices"]:
        raise ProtocolError("participant_device_ids count is invalid")
    if len(set(normalized)) != len(normalized):
        raise ProtocolError("participant_device_ids must be unique")
    local = config["device_id"].casefold()
    primary_folded = primary.casefold()
    if local not in normalized:
        raise ProtocolError("participant_device_ids must include local device")
    if primary_folded not in normalized:
        raise ProtocolError("primary_device_id must be a participant")
    expected_role = "primary" if local == primary_folded else "secondary"
    if role != expected_role:
        raise ProtocolError("role does not match local and primary device")

    _integer(payload.get("start_at_ns"), "start_at_ns", 1)
    duration_ns = _integer(payload.get("slice_duration_ns"), "slice_duration_ns", 1)
    if not config["min_slice_duration_ns"] <= duration_ns <= config["max_slice_duration_ns"]:
        raise ProtocolError("slice_duration_ns is outside configured bounds")


def _validate_stop(raw):
    payload = raw["payload"]
    _identifier(payload.get("request_id"), "request_id", 256)
    _identifier(payload.get("reason"), "reason", 256)
    _identifier(payload.get("job_id"), "job_id")
    _integer(payload.get("stop_at_ns"), "stop_at_ns", 1)


def decode_command(datagram, config):
    if not isinstance(datagram, bytes) or len(datagram) > config["max_datagram_bytes"]:
        raise ProtocolError("datagram exceeds configured limit")
    raw = _unpack(datagram)
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ProtocolError("schema_version is unsupported")
    if raw.get("protocol_id") != config["protocol_id"]:
        raise ProtocolError("protocol_id does not match")
    for key in ("map_id", "device_id", "session_id"):
        _identifier(raw.get(key), key)
    message_type = raw.get("message_type")
    if message_type not in ("start_mapping", "stop_mapping"):
        raise ProtocolError("only mapping commands are accepted")
    _integer(raw.get("sequence"), "sequence")
    _integer(raw.get("sent_at_ns"), "sent_at_ns")
    if not isinstance(raw.get("payload"), dict):
        raise ProtocolError("payload must be a mapping")
    try:
        if message_type == "start_mapping":
            _validate_start(raw, config)
        else:
            _validate_stop(raw)
    except ProtocolError as exc:
        exc.command = raw
        raise
    return raw


def encode_envelope(config, identity, message_type, sequence, payload, sent_at_ns=None):
    if message_type not in MESSAGE_TYPES:
        raise ProtocolError("message_type is invalid")
    map_id = _identifier(identity.get("map_id"), "map_id")
    session_id = _identifier(identity.get("session_id"), "session_id")
    sequence = _integer(sequence, "sequence")
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be a mapping")
    sent_at_ns = now_ns() if sent_at_ns is None else _integer(sent_at_ns, "sent_at_ns")
    raw = {
        "schema_version": 1,
        "protocol_id": config["protocol_id"],
        "map_id": map_id,
        "device_id": config["device_id"],
        "session_id": session_id,
        "message_type": message_type,
        "sequence": sequence,
        "sent_at_ns": sent_at_ns,
        "payload": payload,
    }
    encoded = msgpack.packb(raw, use_bin_type=True)
    if len(encoded) > config["max_datagram_bytes"]:
        raise ProtocolError("datagram exceeds configured limit")
    return encoded


def encode_cloud_chunks(config, identity, first_sequence, metadata, compressed):
    if not isinstance(metadata, dict):
        raise ProtocolError("cloud metadata must be a mapping")
    if not isinstance(compressed, bytes) or not compressed:
        raise ProtocolError("compressed cloud is empty")
    first_sequence = _integer(first_sequence, "first_sequence")
    crc32 = zlib.crc32(compressed) & 0xFFFFFFFF
    chunk_size = max(1, config["max_datagram_bytes"] - 512)
    for unused_attempt in range(32):
        pieces = [
            compressed[index:index + chunk_size]
            for index in range(0, len(compressed), chunk_size)
        ]
        if len(pieces) > 4096:
            raise ProtocolError("cloud requires too many chunks")
        encoded = []
        overflow = 0
        for index, piece in enumerate(pieces):
            payload = dict(metadata)
            payload.update({
                "chunk_count": len(pieces),
                "chunk_index": index,
                "frame_crc32": crc32,
                "data": piece,
            })
            try:
                datagram = encode_envelope(
                    config, identity, "cloud_chunk", first_sequence + index, payload
                )
            except ProtocolError:
                empty = dict(payload, data=b"")
                overhead = len(encode_envelope(
                    config, identity, "cloud_chunk", first_sequence + index, empty
                ))
                overflow = max(
                    overflow, overhead + len(piece) - config["max_datagram_bytes"]
                )
                continue
            encoded.append(datagram)
        if len(encoded) == len(pieces):
            return encoded
        chunk_size -= max(overflow + 8, 16)
        if chunk_size <= 0:
            break
    raise ProtocolError("cannot fit cloud chunks within datagram limit")
