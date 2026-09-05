from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QCoreApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ccs_monitor.models import (
    ConnectionStatus,
    DeviceAvailability,
    DeviceProfile,
    DeviceSnapshot,
    RelocalizationStatus,
)
from ccs_monitor.relocalization_config import load_relocalization_config
from ccs_monitor.relocalization_services import RelocalizationService


TERMINAL_FAILURES = {
    RelocalizationStatus.FAILED,
    RelocalizationStatus.UNSUPPORTED,
}


class ArtifactRepository:
    def __init__(self, root: Path, map_id: str) -> None:
        self.map_id = map_id
        self.directory = self._find(root.resolve(), map_id)
        metadata = json.loads((self.directory / "map.json").read_text(encoding="utf-8"))
        self.pcd = self.directory / str(metadata["pcd_file"])
        pgm = metadata["pgm"]
        self.yaml = self.directory / str(pgm["yaml_file"])
        self.pgm = self.directory / str(pgm["image_file"])
        for path in (self.pcd, self.yaml, self.pgm):
            if not path.is_file() or path.stat().st_size <= 0:
                raise RuntimeError(f"地图产物缺失或为空: {path}")

    @staticmethod
    def _find(root: Path, map_id: str) -> Path:
        for metadata_path in root.glob("*/map.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if metadata.get("map_id") == map_id:
                return metadata_path.parent
        raise RuntimeError(f"在 {root} 中找不到 map_id={map_id}")

    def pcd_path(self, map_id: str) -> Path:
        self._require_map(map_id)
        return self.pcd

    def pgm_paths(self, map_id: str) -> tuple[Path, Path]:
        self._require_map(map_id)
        return self.yaml, self.pgm

    def _require_map(self, map_id: str) -> None:
        if map_id != self.map_id:
            raise KeyError(map_id)


class AcceptanceSource:
    def __init__(self, device_id: str, device_ip: str) -> None:
        self.device_item = DeviceSnapshot(
            device_id,
            "Ground-Air AGV",
            "AGV",
            connection_status=ConnectionStatus.ONLINE,
            ip_address=device_ip,
            availability=DeviceAvailability.AVAILABLE,
        )
        self.profile_item = DeviceProfile(
            device_id,
            "Ground-Air AGV",
            "AGV",
            device_ip,
            availability=DeviceAvailability.AVAILABLE,
            relocalization_profile="ground_air_agv",
        )
        self.logs: list[dict] = []
        self.persisted_bindings: list[dict] = []

    def device(self, device_id: str):
        return self.device_item if device_id.casefold() == self.device_item.device_id.casefold() else None

    def profile(self, device_id: str):
        return self.profile_item if device_id.casefold() == self.profile_item.device_id.casefold() else None

    def append_external_log(self, device_id, level, message) -> None:
        self.logs.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "device_id": device_id,
            "level": getattr(level, "value", str(level)),
            "message": message,
        })

    def set_device_active_map(self, device_id: str, map_id: str | None) -> None:
        if self.profile(device_id) is None:
            raise KeyError(device_id)
        self.profile_item = replace(self.profile_item, active_map_id=map_id)

    def remove_device_map_binding(self, device_id: str, map_id: str) -> None:
        if self.profile(device_id) is None:
            raise KeyError(device_id)
        self.profile_item = replace(
            self.profile_item,
            map_bindings=tuple(
                item for item in self.profile_item.map_bindings if item.map_id != map_id
            ),
        )

    def upsert_device_map_binding(self, device_id: str, binding) -> None:
        if self.profile(device_id) is None:
            raise KeyError(device_id)
        self.profile_item = replace(
            self.profile_item,
            map_bindings=tuple(
                item for item in self.profile_item.map_bindings
                if item.map_id != binding.map_id
            ) + (binding,),
        )
        self.persisted_bindings.append(binding_payload(binding))


def binding_payload(binding) -> dict:
    return {
        "map_id": binding.map_id,
        "map_frame": binding.map_frame,
        "odom_frame": binding.odom_frame,
        "map_from_odom": asdict(binding.map_from_odom),
        "localized_at": binding.localized_at.isoformat(),
        "pose_source": binding.pose_source,
    }


def process_events(app: QCoreApplication, seconds: float = 0.02) -> None:
    app.processEvents()
    time.sleep(seconds)


def wait_for(service, app, map_id, device_id, predicate, timeout, label):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events(app)
        snapshot = service.snapshot(map_id, device_id)
        if predicate(snapshot):
            return snapshot
        if snapshot.status in TERMINAL_FAILURES:
            raise RuntimeError(f"{label}失败: {snapshot.message}")
    snapshot = service.snapshot(map_id, device_id)
    raise TimeoutError(f"等待{label}超时: {snapshot.status.value} / {snapshot.message}")


def collect_tf_samples(service, app, map_id, device_id, duration) -> list[dict]:
    samples: list[dict] = []
    last_localized_at = None
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        process_events(app)
        snapshot = service.snapshot(map_id, device_id)
        if snapshot.status == RelocalizationStatus.FAILED:
            raise RuntimeError(f"持续 TF 上报失败: {snapshot.message}")
        binding = service.binding(map_id, device_id)
        if binding is None or binding.localized_at == last_localized_at:
            continue
        last_localized_at = binding.localized_at
        payload = binding_payload(binding)
        payload["received_monotonic"] = time.monotonic()
        samples.append(payload)
    return samples


def validate_samples(samples: list[dict], duration: float) -> dict:
    minimum = max(2, int(math.floor(duration)) - 2)
    if len(samples) < minimum:
        raise RuntimeError(f"仅收到 {len(samples)} 个 TF 样本，期望至少 {minimum} 个")
    for sample in samples:
        if sample["map_frame"] != "map" or sample["odom_frame"] != "odom":
            raise RuntimeError(
                f"TF frame 契约错误: {sample['map_frame']} <- {sample['odom_frame']}"
            )
        if not all(math.isfinite(value) for value in sample["map_from_odom"].values()):
            raise RuntimeError("TF 样本包含非有限数值")
    intervals = [
        current["received_monotonic"] - previous["received_monotonic"]
        for previous, current in zip(samples, samples[1:])
    ]
    median = statistics.median(intervals) if intervals else None
    if median is not None and not 0.65 <= median <= 1.35:
        raise RuntimeError(f"TF 上报中位间隔 {median:.3f}s 不符合约 1 Hz 契约")
    return {
        "sample_count": len(samples),
        "intervals_seconds": intervals,
        "median_interval_seconds": median,
    }


def run_cycle(service, app, map_id, device_id, sample_seconds, cycle_index):
    service.start_stack(map_id, device_id)
    duplicate = next(
        pending for pending in service._pending.values()
        if pending.envelope.message_type == "start_stack"
    )
    service._send_pending(duplicate)
    wait_for(
        service, app, map_id, device_id,
        lambda item: item.status == RelocalizationStatus.AWAITING_POSE,
        150.0, f"第 {cycle_index} 轮栈启动",
    )
    service.submit_initial_pose(map_id, device_id, 0.0, 0.0, 0.0)
    wait_for(
        service, app, map_id, device_id,
        lambda item: item.status == RelocalizationStatus.SUCCEEDED,
        120.0, f"第 {cycle_index} 轮零位姿重定位",
    )
    samples = collect_tf_samples(
        service, app, map_id, device_id, sample_seconds
    )
    return {
        "cycle": cycle_index,
        "initial_pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "tf": validate_samples(samples, sample_seconds),
        "samples": samples,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ground-Air AGV 重定位静态增量验收（允许重发旧 TF）"
    )
    parser.add_argument("--device", default="192.168.50.130")
    parser.add_argument("--device-id", default="AGV_001")
    parser.add_argument("--map-root", type=Path, required=True)
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--config", type=Path, default=Path("config/relocalization.json"))
    parser.add_argument("--sample-seconds", type=float, default=10.5)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sample_seconds < 3.0 or args.cycles < 1:
        raise SystemExit("sample-seconds 必须 >= 3 且 cycles 必须 >= 1")
    args.output.mkdir(parents=True, exist_ok=True)
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    source = AcceptanceSource(args.device_id, args.device)
    repository = ArtifactRepository(args.map_root, args.map_id)
    service = RelocalizationService(
        load_relocalization_config(args.config), repository, source
    )
    events: list[dict] = []
    warnings: list[str] = []
    service.snapshot_updated.connect(lambda item: events.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "status": item.status.value,
        "message": item.message,
        "session_id": item.session_id,
    }))
    service.protocol_warning.connect(warnings.append)
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "device_id": args.device_id,
        "device_ip": args.device,
        "map_id": args.map_id,
        "map_directory": str(repository.directory),
        "cycles": [],
        "passed": False,
    }
    exit_code = 1
    try:
        service.start()
        if not service.available:
            raise RuntimeError(service.module_message)
        service.negotiate(args.map_id, args.device_id)
        snapshot = wait_for(
            service, app, args.map_id, args.device_id,
            lambda item: item.status in {
                RelocalizationStatus.MAP_READY,
                RelocalizationStatus.SUCCEEDED,
            } or item.can_download,
            35.0, "地图协商",
        )
        if snapshot.can_download:
            service.download_map(args.map_id, args.device_id)
            wait_for(
                service, app, args.map_id, args.device_id,
                lambda item: item.status == RelocalizationStatus.MAP_READY,
                180.0, "地图下发与校验",
            )
        for cycle in range(1, args.cycles + 1):
            report["cycles"].append(run_cycle(
                service, app, args.map_id, args.device_id,
                args.sample_seconds, cycle,
            ))
        report["passed"] = True
        exit_code = 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        service.stop()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["protocol_warnings"] = warnings
        report["source_logs"] = source.logs
        report["persisted_bindings"] = source.persisted_bindings
        (args.output / "events.json").write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
