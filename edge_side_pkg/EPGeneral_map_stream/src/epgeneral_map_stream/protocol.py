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


def decode_command(datagram, config):
    if len(datagram) > config["max_datagram_bytes"]:
        raise ProtocolError("datagram exceeds configured limit")
    try:
        try:
            raw = msgpack.unpackb(datagram, raw=False, strict_map_key=True)
        except TypeError:
            # Ubuntu 18.04 ships msgpack 0.5.x, before strict_map_key existed.
            raw = msgpack.unpackb(datagram, raw=False)
    except Exception as exc:
        raise ProtocolError("MessagePack decode failed: %s" % exc)
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
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be a mapping")
    _identifier(payload.get("request_id"), "request_id", 256)
    if message_type == "start_mapping":
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
    else:
        _identifier(payload.get("reason"), "reason", 256)
    return raw


def encode_envelope(config, identity, message_type, sequence, payload):
    if message_type not in MESSAGE_TYPES:
        raise ProtocolError("message_type is invalid")
    raw = {
        "schema_version": 1,
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
    if not compressed:
        raise ProtocolError("compressed cloud is empty")
    crc32 = zlib.crc32(compressed) & 0xFFFFFFFF
    chunk_size = max(1, config["max_datagram_bytes"] - 512)
    for unused_attempt in range(32):
        pieces = [compressed[index:index + chunk_size] for index in range(0, len(compressed), chunk_size)]
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
                datagram = encode_envelope(config, identity, "cloud_chunk", first_sequence + index, payload)
            except ProtocolError:
                raw = dict(payload)
                raw["data"] = b""
                overhead = len(encode_envelope(config, identity, "cloud_chunk", first_sequence + index, raw))
                overflow = max(overflow, overhead + len(piece) - config["max_datagram_bytes"])
                continue
            encoded.append(datagram)
        if len(encoded) == len(pieces):
            return encoded
        chunk_size -= max(overflow + 8, 16)
        if chunk_size <= 0:
            break
    raise ProtocolError("cannot fit cloud chunks within datagram limit")
