"""Resume the stop/download phase for an interrupted static AGV acceptance run."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
import urllib.request
from pathlib import Path

from agv_mapping_incremental_test import envelope, wait_for


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="192.168.50.130")
    parser.add_argument("--return-host", default="192.168.50.101")
    parser.add_argument("--return-port", type=int, default=14562)
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    events = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.return_host, args.return_port))
    try:
        for attempt in range(1, 4):
            request_id = "resume-stop-{}-{}".format(attempt, args.session_id)
            sock.sendto(envelope(args.map_id, args.session_id, "stop_mapping",
                                 100 + attempt, {
                                     "request_id": request_id,
                                     "reason": "resume interrupted static acceptance",
                                 }), (args.device, 14561))
            result = wait_for(
                sock, time.monotonic() + 150.0,
                lambda item: item.get("message_type") == "artifact_status"
                and item.get("payload", {}).get("state") in {"ready", "error"},
                events)
            if result["payload"].get("state") == "ready":
                break
            wait_for(
                sock, time.monotonic() + 15.0,
                lambda item: item.get("message_type") == "session_status"
                and item.get("payload", {}).get("state") == "mapping", events)
        else:
            raise RuntimeError("map save failed after three retries")

        args.output.mkdir(parents=True, exist_ok=True)
        archive = args.output / (args.map_id + ".zip")
        with urllib.request.urlopen(result["payload"]["url"], timeout=60) as response:
            content = response.read()
        archive.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        if (len(content) != result["payload"]["byte_count"]
                or digest != result["payload"]["sha256"]):
            raise RuntimeError("downloaded artifact size or SHA-256 mismatch")
        report = args.output / (args.map_id + ".json")
        report.write_text(json.dumps({
            "map_id": args.map_id,
            "session_id": args.session_id,
            "archive": str(archive),
            "byte_count": len(content),
            "sha256": digest,
            "events": events,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"result": "PASS", "archive": str(archive),
                          "byte_count": len(content), "sha256": digest},
                         ensure_ascii=False))
        return 0
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
