from __future__ import absolute_import

import math

import msgpack


MESSAGE_TYPES = set((
    "negotiate", "negotiation_status", "map_offer", "download_status",
    "start_stack", "stack_status", "initial_pose", "relocalization_result",
    "session_heartbeat", "command_error",
))


class ProtocolError(ValueError):
    pass


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError("payload contains a non-finite number")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


class Protocol(object):
    def __init__(self, protocol_id, max_bytes):
        self.protocol_id = protocol_id
        self.max_bytes = max_bytes

    def encode(self, message):
        self.validate(message)
        raw = dict(message)
        raw.update(schema_version=1, protocol_id=self.protocol_id)
        result = msgpack.packb(raw, use_bin_type=True)
        if len(result) > self.max_bytes:
            raise ProtocolError("datagram exceeds configured limit")
        return result

    def decode(self, data):
        if len(data) > self.max_bytes:
            raise ProtocolError("datagram exceeds configured limit")
        try:
            raw = msgpack.unpackb(data, raw=False, strict_map_key=True)
        except Exception as exc:
            raise ProtocolError("MessagePack decode failed: %s" % exc)
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ProtocolError("schema is invalid")
        if raw.get("protocol_id") != self.protocol_id:
            raise ProtocolError("protocol id does not match")
        message = {key: raw.get(key) for key in (
            "map_id", "device_id", "session_id", "request_id", "message_type",
            "sequence", "sent_at_ns", "payload")}
        self.validate(message)
        return message

    def validate(self, message):
        for key in ("map_id", "device_id", "session_id", "request_id"):
            value = message.get(key)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ProtocolError("%s is invalid" % key)
        if message.get("message_type") not in MESSAGE_TYPES:
            raise ProtocolError("message type is invalid")
        if not isinstance(message.get("sequence"), int) or message["sequence"] < 0:
            raise ProtocolError("sequence is invalid")
        if not isinstance(message.get("sent_at_ns"), int) or message["sent_at_ns"] < 0:
            raise ProtocolError("sent_at_ns is invalid")
        if not isinstance(message.get("payload"), dict):
            raise ProtocolError("payload is invalid")
        _finite(message["payload"])
