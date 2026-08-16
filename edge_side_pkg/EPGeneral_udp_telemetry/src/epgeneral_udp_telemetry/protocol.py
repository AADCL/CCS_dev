import time

import msgpack


class ProtocolError(ValueError):
    pass


def encode_envelope(config, session_id, message_type, sequence, payload=None, level=None):
    envelope = {
        "schema_version": 1,
        "protocol_id": config["protocol_id"],
        "descriptor_hash": config["descriptor_hash"],
        "device_id": config["device_id"],
        "session_id": session_id,
        "message_type": message_type,
        "sequence": int(sequence),
        "sent_at_ns": int(time.time() * 1000000000),
        "level": level,
        "payload": payload or {},
    }
    encoded = msgpack.packb(envelope, use_bin_type=True)
    if len(encoded) > config["max_datagram_bytes"]:
        raise ProtocolError("datagram exceeds configured limit")
    return encoded
