"""Non-motion incremental acceptance test for the Ground-Air AGV mapping flow."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import socket
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

import msgpack

PREVIEW_FRAME = "odom"
PREVIEW_SOURCE_FRAME = "camera_init"
ARTIFACT_FRAME = "map"


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


def validate_prepare_contract(payload: dict) -> None:
    actual = payload.get("frame_id")
    if actual != PREVIEW_FRAME:
        raise RuntimeError(
            "prepare frame mismatch: expected %s, got %r"
            % (PREVIEW_FRAME, actual)
        )


def validate_fragment_contract(payload: dict) -> None:
    actual = (payload.get("frame_id"), payload.get("source_frame_id"))
    expected = (PREVIEW_FRAME, PREVIEW_SOURCE_FRAME)
    if actual != expected:
        raise RuntimeError(
            "fragment frame mismatch: expected %r, got %r" % (expected, actual)
        )
    transform = payload.get("display_from_source")
    keys = ("x", "y", "z", "qx", "qy", "qz", "qw")
    if not isinstance(transform, dict) or any(
            not isinstance(transform.get(key), (int, float))
            or not math.isfinite(float(transform[key]))
            for key in keys):
        raise RuntimeError("fragment display_from_source is incomplete or invalid")


def validate_fragment_content(payload: dict, content: bytes) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != payload.get("byte_count"):
        raise RuntimeError("fragment byte count mismatch")
    if digest != payload.get("sha256"):
        raise RuntimeError("fragment SHA-256 mismatch")
    marker = b"DATA binary\n"
    marker_offset = content.find(marker)
    if marker_offset < 0:
        raise RuntimeError("fragment is not a binary PCD")
    header = content[:marker_offset].decode("ascii", errors="strict")
    for required in ("FIELDS x y z", "SIZE 4 4 4", "TYPE F F F"):
        if required not in header.splitlines():
            raise RuntimeError("fragment PCD header is missing %s" % required)
    point_count = payload.get("point_count")
    if not isinstance(point_count, int) or point_count < 0:
        raise RuntimeError("fragment point count is invalid")
    if "POINTS %d" % point_count not in header.splitlines():
        raise RuntimeError("fragment PCD point count does not match descriptor")
    binary = content[marker_offset + len(marker):]
    if len(binary) != point_count * 12:
        raise RuntimeError("fragment binary XYZ payload length is invalid")
    return {
        "byte_count": len(content),
        "sha256": digest,
        "point_count": point_count,
    }


def download_fragment(payload: dict) -> dict:
    url = payload.get("url")
    if not isinstance(url, str) or not url:
        raise RuntimeError("fragment URL is missing")
    with urllib.request.urlopen(url, timeout=30) as response:
        content = response.read()
    return validate_fragment_content(payload, content)


def validate_artifact(content: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        required = {"manifest.json", "map.pgm", "map.yaml"}
        missing = sorted(required - names)
        if missing:
            raise RuntimeError("artifact is missing required files: %s" % missing)
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("frame_id") != ARTIFACT_FRAME:
            raise RuntimeError(
                "artifact frame mismatch: expected %s, got %r"
                % (ARTIFACT_FRAME, manifest.get("frame_id"))
            )
        for kind in ("pgm", "yaml"):
            descriptor = manifest.get("files", {}).get(kind, {})
            path = descriptor.get("path")
            if not path or path not in names:
                raise RuntimeError("artifact manifest has no valid %s entry" % kind)
            data = archive.read(path)
            if len(data) != descriptor.get("byte_count"):
                raise RuntimeError("artifact %s byte count mismatch" % kind)
            if hashlib.sha256(data).hexdigest() != descriptor.get("sha256"):
                raise RuntimeError("artifact %s SHA-256 mismatch" % kind)
        return manifest


def wait_for(sock: socket.socket, deadline: float, predicate, events: list[dict],
             fragment_handler=None) -> dict:
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
            "frame_id": payload.get("frame_id"),
            "source_frame_id": payload.get("source_frame_id"),
            "display_from_source": payload.get("display_from_source"),
        }, ensure_ascii=False), flush=True)
        if item.get("message_type") == "cloud_fragment_ready":
            if fragment_handler is not None:
                fragment_handler(payload)
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
        validate_prepare_contract(prepared["payload"])

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
        fragment_summary = {}

        def validate_first_fragment(payload):
            validate_fragment_contract(payload)
            fragment_summary.update(download_fragment(payload))

        first_fragment = wait_for(
            sock, sample_deadline,
            lambda item: item.get("message_type") == "cloud_fragment_ready",
            events,
            fragment_handler=validate_first_fragment,
        )
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
        manifest = validate_artifact(content)
        report = args.output / (map_id + ".json")
        report.write_text(json.dumps({
            "map_id": map_id,
            "session_id": session_id,
            "archive": str(archive),
            "byte_count": len(content),
            "sha256": digest,
            "frame_contract": {
                "prepare": prepared["payload"]["frame_id"],
                "preview": first_fragment["payload"]["frame_id"],
                "preview_source": first_fragment["payload"]["source_frame_id"],
                "artifact": manifest["frame_id"],
            },
            "first_fragment": fragment_summary,
            "events": events,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"result": "PASS", "archive": str(archive),
                          "byte_count": len(content), "sha256": digest,
                          "frame_contract": {
                              "prepare": prepared["payload"]["frame_id"],
                              "preview": first_fragment["payload"]["frame_id"],
                              "preview_source": first_fragment["payload"]["source_frame_id"],
                              "artifact": manifest["frame_id"],
                          }}, ensure_ascii=False))
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
