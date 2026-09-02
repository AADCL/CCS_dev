"""Non-motion incremental acceptance test for the Ground-Air AGV mapping flow."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
import urllib.request
import uuid
from pathlib import Path

import msgpack


def envelope(map_id: str, session_id: str, message_type: str, sequence: int,
             payload: dict) -> bytes:
    return msgpack.packb({
        "schema_version": 2,
        "protocol_id": "ccs-map-stream-v2",
        "map_id": map_id,
        "device_id": "AGV_001",
        "session_id": session_id,
        "message_type": message_type,
        "sequence": sequence,
        "sent_at_ns": time.time_ns(),
        "payload": payload,
    }, use_bin_type=True)


def decode(datagram: bytes) -> dict:
    return msgpack.unpackb(datagram, raw=False, strict_map_key=True)


def wait_for(sock: socket.socket, deadline: float, predicate, events: list[dict]) -> dict:
    while time.monotonic() < deadline:
        sock.settimeout(max(0.1, min(2.0, deadline - time.monotonic())))
        try:
            datagram, peer = sock.recvfrom(65535)
        except socket.timeout:
            continue
        item = decode(datagram)
        item["_peer"] = peer[0]
        events.append(item)
        payload = item.get("payload", {})
        print(json.dumps({
            "rx": item.get("message_type"),
            "sequence": item.get("sequence"),
            "state": payload.get("state"),
            "accepted": payload.get("accepted"),
            "error_code": payload.get("error_code"),
            "reason": payload.get("reason"),
        }, ensure_ascii=False), flush=True)
        if item.get("message_type") == "cloud_fragment_ready":
            fragment_id = payload.get("fragment_id")
            ack = envelope(item["map_id"], item["session_id"], "cloud_fragment_ack", 9000 + int(fragment_id), {
                "request_id": "fragment-ack-%s" % fragment_id,
                "fragment_id": fragment_id,
            })
            sock.sendto(ack, (peer[0], 14561))
        if predicate(item):
            return item
    raise TimeoutError("timed out waiting for expected mapping response")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="192.168.50.130")
    parser.add_argument("--return-host", default="192.168.50.101")
    parser.add_argument("--return-port", type=int, default=14562)
    parser.add_argument("--sample-seconds", type=float, default=12.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/agv_incremental_test"))
    args = parser.parse_args()

    map_id = "agv-static-%s" % time.strftime("%Y%m%d-%H%M%S")
    session_id = uuid.uuid4().hex
    target = (args.device, 14561)
    events: list[dict] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.return_host, args.return_port))
    print(json.dumps({"map_id": map_id, "session_id": session_id,
                      "listen": "%s:%d" % (args.return_host, args.return_port)}, ensure_ascii=False))
    try:
        prepare = envelope(map_id, session_id, "prepare_mapping", 0, {
            "request_id": "prepare-" + session_id,
            "return_host": args.return_host,
            "return_port": args.return_port,
            "required_inputs": ["pointcloud", "imu", "artifact_storage", "map_generation"],
            "preview_transport": "pcd_fragment_http",
            "fragment_interval_seconds": 1.0,
        })
        sock.sendto(prepare, target)
        prepared = wait_for(
            sock, time.monotonic() + 15.0,
            lambda item: item.get("message_type") == "prepare_result", events)
        if not prepared["payload"].get("accepted"):
            raise RuntimeError("prepare rejected: %s" % prepared["payload"])

        start = envelope(map_id, session_id, "start_mapping", 1, {
            "request_id": "start-" + session_id,
            "coordinate_contract": "sensor+map_body+body_sensor",
            "preview_transport": "pcd_fragment_http",
            "fragment_interval_seconds": 1.0,
        })
        sock.sendto(start, target)
        wait_for(sock, time.monotonic() + 90.0,
                 lambda item: item.get("message_type") == "session_status"
                 and item.get("payload", {}).get("state") == "mapping", events)

        sock.sendto(start, target)
        duplicate = wait_for(
            sock, time.monotonic() + 10.0,
            lambda item: item.get("message_type") == "command_ack"
            and item.get("payload", {}).get("request_id") == "start-" + session_id, events)
        if not duplicate["payload"].get("accepted"):
            raise RuntimeError("duplicate start was not idempotently accepted")

        sample_deadline = time.monotonic() + args.sample_seconds
        try:
            wait_for(sock, sample_deadline, lambda unused: False, events)
        except TimeoutError:
            pass

        ready = None
        for attempt in range(1, 4):
            stop = envelope(map_id, session_id, "stop_mapping", 10 + attempt, {
                "request_id": "stop-%d-%s" % (attempt, session_id),
                "reason": "static incremental acceptance test",
            })
            sock.sendto(stop, target)
            result = wait_for(
                sock, time.monotonic() + 150.0,
                lambda item: item.get("message_type") == "artifact_status"
                and item.get("payload", {}).get("state") in {"ready", "error"}, events)
            if result["payload"].get("state") == "ready":
                ready = result
                break
            wait_for(
                sock, time.monotonic() + 15.0,
                lambda item: item.get("message_type") == "session_status"
                and item.get("payload", {}).get("state") == "mapping", events)
        if ready is None:
            raise RuntimeError("map save failed after three non-destructive retries")

        args.output.mkdir(parents=True, exist_ok=True)
        archive = args.output / (map_id + ".zip")
        with urllib.request.urlopen(ready["payload"]["url"], timeout=60) as response:
            content = response.read()
        archive.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != ready["payload"]["byte_count"] or digest != ready["payload"]["sha256"]:
            raise RuntimeError("downloaded artifact size or SHA-256 mismatch")
        report = args.output / (map_id + ".json")
        report.write_text(json.dumps({
            "map_id": map_id,
            "session_id": session_id,
            "archive": str(archive),
            "byte_count": len(content),
            "sha256": digest,
            "events": events,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"result": "PASS", "archive": str(archive),
                          "byte_count": len(content), "sha256": digest}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"result": "FAIL", "error": str(exc)}, ensure_ascii=False), flush=True)
        abort = envelope(map_id, session_id, "abort_mapping", 99, {
            "request_id": "abort-" + session_id,
            "reason": "incremental acceptance test cleanup",
        })
        try:
            sock.sendto(abort, target)
        except OSError:
            pass
        return 1
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
