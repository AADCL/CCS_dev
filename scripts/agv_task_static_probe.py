"""Send a non-executing CCS task-control probe to an AGV edge device."""

from __future__ import annotations

import argparse
import json
import socket
import time
import uuid
import zlib

import msgpack


def envelope(args: argparse.Namespace, message_type: str, request_id: str, payload: dict) -> bytes:
    return msgpack.packb(
        {
            "schema_version": 2,
            "protocol_id": "ccs-task-control-v2",
            "task_id": args.task_id,
            "subtask_id": args.subtask_id,
            "device_id": args.device_id,
            "execution_id": "",
            "message_type": message_type,
            "request_id": request_id,
            "sequence": 1,
            "sent_at_ns": time.time_ns(),
            "payload": payload,
        },
        use_bin_type=True,
    )


def transfer_messages(args: argparse.Namespace, request_id: str) -> list[tuple[str, bytes]]:
    trajectory = {
        "schema_version": 2,
        "task_id": args.task_id,
        "task_name": "AGV static acceptance probe",
        "map_id": args.map_id,
        "frame_id": "map",
        "subtask_id": args.subtask_id,
        "device_id": args.device_id,
        "revision": args.revision,
        "task_type": "ground",
        "cruise_speed_mps": args.speed,
        "start_delay_seconds": 0.0,
        "waypoints": [
            {"index": 0, "waypoint_id": "static-start", "x": args.x, "y": args.y, "z": 0.0},
            {"index": 1, "waypoint_id": "static-end", "x": args.x, "y": args.y, "z": 0.0},
        ],
    }
    raw = json.dumps(trajectory, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw)
    checksum = zlib.crc32(compressed) & 0xFFFFFFFF
    chunks = [compressed[offset : offset + 800] for offset in range(0, len(compressed), 800)]
    common = {
        "revision": args.revision,
        "chunk_count": len(chunks),
        "crc32": checksum,
    }
    messages = [
        (
            "task_prepare",
            envelope(
                args,
                "task_prepare",
                request_id + "-prepare",
                dict(
                    common,
                    compressed_bytes=len(compressed),
                    raw_bytes=len(raw),
                    compression="zlib",
                    encoding="json-utf8",
                ),
            ),
        )
    ]
    for index, chunk in enumerate(chunks):
        messages.append(
            (
                "task_chunk",
                envelope(
                    args,
                    "task_chunk",
                    request_id + "-chunk",
                    dict(common, chunk_index=index, data=chunk),
                ),
            )
        )
    messages.append(
        (
            "task_commit",
            envelope(args, "task_commit", request_id + "-commit", common),
        )
    )
    return messages


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("negotiate", "transfer", "delete"))
    parser.add_argument("--device-host", default="192.168.50.130")
    parser.add_argument("--device-port", type=int, default=14563)
    parser.add_argument("--device-id", default="AGV_001")
    parser.add_argument("--task-id", default="agv-static-acceptance")
    parser.add_argument("--subtask-id", default="agv-static-acceptance-AGV_001")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--map-id", default="")
    parser.add_argument("--speed", type=float, default=0.05)
    parser.add_argument("--x", type=float, default=0.0)
    parser.add_argument("--y", type=float, default=0.0)
    parser.add_argument("--delay", type=float, default=0.1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    request_id = "agv-static-" + uuid.uuid4().hex[:12]
    if args.action == "transfer":
        if not args.map_id:
            raise SystemExit("--map-id is required for transfer")
        messages = transfer_messages(args, request_id)
    else:
        message_type = "negotiate_task" if args.action == "negotiate" else "delete_task"
        messages = [(message_type, envelope(args, message_type, request_id, {"revision": args.revision}))]
    destination = (args.device_host, args.device_port)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = []
    for message_type, datagram in messages:
        sent.append({"message_type": message_type, "bytes": udp.sendto(datagram, destination)})
        time.sleep(args.delay)
    print(json.dumps({"request_id": request_id, "destination": destination, "sent": sent}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
