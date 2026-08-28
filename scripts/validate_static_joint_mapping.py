"""Run a no-motion, disposable end-to-end joint mapping validation."""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication

from ccs_monitor.map_building_config import load_map_building_config
from ccs_monitor.map_building_services import MapBuildingService
from ccs_monitor.map_fusion import MapFusionRepository
from ccs_monitor.map_repository import MapRepository
from ccs_monitor.models import (
    DeviceSnapshot, MapCreatorDevice, MapStatus, MapTransform,
)


def wait_until(app, predicate, timeout: float, label: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise TimeoutError("timed out waiting for " + label)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary-ip", required=True)
    parser.add_argument("--secondary-ip", required=True)
    parser.add_argument("--minimum-fragments", type=int, default=3)
    parser.add_argument("--mapping-timeout", type=float, default=240.0)
    parser.add_argument("--artifact-timeout", type=float, default=600.0)
    args = parser.parse_args()
    if args.minimum_fragments < 3:
        parser.error("minimum-fragments must be at least 3")

    app = QCoreApplication.instance() or QCoreApplication([])
    config = load_map_building_config()
    errors: list[str] = []
    completed = []
    with tempfile.TemporaryDirectory(prefix="ccs-static-joint-validation-") as directory:
        root = Path(directory)
        repository = MapRepository(root / "maps")
        fusion_repository = MapFusionRepository(
            root / "fusion.json", root / "algorithms"
        )
        service = MapBuildingService(
            config, repository, fusion_repository=fusion_repository
        )
        service.failed.connect(errors.append)
        service.completed.connect(completed.append)
        service.start()
        try:
            if not service.available:
                raise RuntimeError(service.module_message)
            devices = (
                DeviceSnapshot(
                    "UGV_001", "Scout", "UGV", ip_address=args.primary_ip
                ),
                DeviceSnapshot(
                    "UGV_003", "WheelTech", "UGV", ip_address=args.secondary_ip
                ),
            )
            definition = repository.create(
                "static_joint_validation", tuple(
                    MapCreatorDevice(item.device_id, item.device_name, item.device_type)
                    for item in devices
                )
            )
            transforms = (
                MapTransform("UGV_001", True),
                MapTransform("UGV_003", False, (0.0, -1.2, 0.0), (0.0, 0.0, 0.0)),
            )
            service.prepare_joint_remote_mapping(
                definition, devices, "UGV_001", transforms,
                fusion_repository.default_algorithm(),
            )
            def all_ready():
                if errors:
                    raise RuntimeError(errors[-1])
                snapshot = service.current_remote_snapshot
                return snapshot if snapshot and snapshot.state == "ready" else None

            ready = wait_until(app, all_ready, 90.0, "both devices ready")
            print("READY capability=" + ready.capability_version, flush=True)
            service.begin_remote_mapping()

            def enough_fragments():
                snapshots = service.current_remote_device_snapshots
                if errors:
                    raise RuntimeError(errors[-1])
                if len(snapshots) != 2:
                    return None
                counts = {key: value.complete_frames for key, value in snapshots.items()}
                if all(value >= args.minimum_fragments for value in counts.values()):
                    return counts
                return None

            counts = wait_until(
                app, enough_fragments, args.mapping_timeout, "three previews per device"
            )
            print("PREVIEWS " + " ".join(
                "%s=%d" % item for item in sorted(counts.items())
            ), flush=True)
            service.stop_remote_mapping("静态联合建图验证正常结束")

            def committed():
                if completed:
                    return completed[-1]
                if errors:
                    raise RuntimeError(errors[-1])
                return None

            result = wait_until(
                app, committed, args.artifact_timeout, "joint artifact commit"
            )
            stored = repository.map_by_id(result.map_id)
            if (stored is None or stored.status != MapStatus.READY
                    or not stored.pcd_path or stored.pgm is None
                    or stored.build_provenance is None
                    or stored.pgm_fusion is None):
                raise RuntimeError("committed map is incomplete")
            if repository.active_map_id() is not None:
                raise RuntimeError("disposable validation map became active")
            output = repository.root / stored.directory_name
            for name in ("map.pcd", "map.pgm", "map.yaml", "map.json"):
                path = output / name
                if not path.is_file() or path.stat().st_size <= 0:
                    raise RuntimeError("missing committed artifact: " + name)
            print(
                "COMMITTED points=%d excluded=%s active_map=none" % (
                    stored.point_count,
                    ",".join(stored.build_provenance.excluded_device_ids) or "none",
                ),
                flush=True,
            )
        finally:
            service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
