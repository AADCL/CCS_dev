import json
import math
import tempfile
import unittest
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from ccs_monitor.device_config import DeviceConfigRepository
from ccs_monitor.models import (
    ConnectionStatus, DeviceMapBinding, DeviceProfile, DeviceSnapshot, FrameTransform,
    RelocalizationStatus,
)
from ccs_monitor.relocalization_artifacts import build_relocalization_archive
from ccs_monitor.relocalization_config import load_relocalization_config
from ccs_monitor.relocalization_protocol import (
    RelocalizationEnvelope, RelocalizationProtocol, RelocalizationProtocolError,
)
from ccs_monitor.relocalization_services import RelocalizationService, RelocalizationSnapshot


class _FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, peer):
        self.sent.append((data, peer))


class _FakeSource:
    def __init__(self):
        self.device_item = DeviceSnapshot(
            "UGV_001", "Scout", "UGV", ip_address="192.168.50.120",
            connection_status=ConnectionStatus.ONLINE,
        )
        self.profile_item = DeviceProfile(
            "UGV_001", "Scout", "UGV", "192.168.50.120",
            relocalization_profile="scout_mini",
        )
        self.logs = []
        self.removed_bindings = []
        self.saved_bindings = []

    def device(self, device_id):
        return self.device_item if device_id.casefold() == "ugv_001" else None

    def profile(self, device_id):
        return self.profile_item if device_id.casefold() == "ugv_001" else None

    def append_external_log(self, *args):
        self.logs.append(args)

    def remove_device_map_binding(self, device_id, map_id):
        self.removed_bindings.append((device_id, map_id))
        self.profile_item = replace(
            self.profile_item,
            map_bindings=tuple(
                item for item in self.profile_item.map_bindings if item.map_id != map_id
            ),
        )

    def upsert_device_map_binding(self, device_id, binding):
        self.saved_bindings.append((device_id, binding))
        self.profile_item = replace(
            self.profile_item,
            map_bindings=tuple(
                item for item in self.profile_item.map_bindings if item.map_id != binding.map_id
            ) + (binding,),
        )


class _CompleteMapRepository:
    def pcd_path(self, _map_id):
        return Path("public_map.pcd")

    def pgm_paths(self, _map_id):
        return Path("map.yaml"), Path("map.pgm")


class RelocalizationProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol = RelocalizationProtocol(load_relocalization_config())

    def test_round_trip_and_non_finite_rejection(self):
        envelope = RelocalizationEnvelope(
            "map-1", "UGV_001", "session", "request", "initial_pose", 1, 2,
            {"x": 1.0, "y": 2.0, "yaw": 0.5},
        )
        self.assertEqual(self.protocol.decode(self.protocol.encode(envelope)), envelope)
        with self.assertRaises(RelocalizationProtocolError):
            self.protocol.encode(RelocalizationEnvelope(
                "map-1", "UGV_001", "session", "request", "initial_pose", 1, 2,
                {"x": math.nan},
            ))


class DeviceBindingTests(unittest.TestCase):
    def test_schema_four_migrates_and_binding_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            path.write_text(json.dumps({
                "schema_version": 4,
                "devices": [{
                    "device_id": "UGV_001", "device_name": "Scout", "device_type": "UGV",
                    "ip_address": "127.0.0.1", "srt_port": 9000, "srt_latency_ms": 120,
                }],
            }), encoding="utf-8")
            repository = DeviceConfigRepository(path)
            profile = repository.load()[0]
            self.assertEqual(profile.relocalization_profile, "disabled")
            binding = DeviceMapBinding(
                "map-1", "map", "odom", FrameTransform(1, 2, 0, 0, 0, 0, 1),
                datetime.now(timezone.utc), "vision_pose",
            )
            repository.upsert_map_binding(profile.device_id, binding)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], 6)
            self.assertEqual(saved["devices"][0]["map_bindings"][0]["map_id"], "map-1")
            self.assertEqual(repository.load()[0].map_bindings[0], binding)

    def test_binding_can_be_removed_for_only_one_map(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.json"
            repository = DeviceConfigRepository(path)
            repository._profiles = [DeviceProfile(
                "UGV_001", "Scout", "UGV", "127.0.0.1",
                map_bindings=(
                    DeviceMapBinding(
                        "map-1", "map", "odom", FrameTransform(1, 2, 0, 0, 0, 0, 1),
                        datetime.now(timezone.utc), "vision_pose",
                    ),
                    DeviceMapBinding(
                        "map-2", "map", "odom", FrameTransform(3, 4, 0, 0, 0, 0, 1),
                        datetime.now(timezone.utc), "vision_pose",
                    ),
                ),
            )]
            repository.remove_map_binding("ugv_001", "map-1")
            self.assertEqual(
                [item.map_id for item in repository.load()[0].map_bindings], ["map-2"]
            )


class RelocalizationArchiveTests(unittest.TestCase):
    def test_archive_uses_canonical_names_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pcd = root / "map.pcd"
            pcd.write_text(
                "VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\n"
                "WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n0 0 0\n", encoding="ascii")
            pgm = root / "map.pgm"
            pgm.write_bytes(b"P5\n1 1\n255\n\x00")
            yaml_path = root / "map.yaml"
            yaml_path.write_text(
                "image: map.pgm\nresolution: 1.0\norigin: [0, 0, 0]\n"
                "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")

            class Repository:
                def pcd_path(self, _map_id):
                    return pcd

                def pgm_paths(self, _map_id):
                    return yaml_path, pgm

            archive, descriptor = build_relocalization_archive(Repository(), "map-1", 1024 * 1024)
            self.addCleanup(archive.unlink, missing_ok=True)
            with zipfile.ZipFile(archive) as value:
                self.assertEqual(set(value.namelist()), {
                    "manifest.json", "public_map.pcd", "map.pgm", "map.yaml"})
                manifest = json.loads(value.read("manifest.json"))
            self.assertEqual(manifest["map_id"], "map-1")
            self.assertEqual(descriptor["byte_count"], archive.stat().st_size)

    def test_yaml_image_must_be_canonical_package_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "map.pcd").write_text(
                "FIELDS x y z\nPOINTS 1\nDATA ascii\n0 0 0\n", encoding="ascii"
            )
            (root / "map.pgm").write_bytes(b"P5\n1 1\n255\n\x00")
            (root / "map.yaml").write_text("image: ../map.pgm\n", encoding="utf-8")

            class Repository:
                def pcd_path(self, _map_id):
                    return root / "map.pcd"

                def pgm_paths(self, _map_id):
                    return root / "map.yaml", root / "map.pgm"

            with self.assertRaisesRegex(RuntimeError, "YAML"):
                build_relocalization_archive(Repository(), "map-1", 1024 * 1024)


class RelocalizationServiceTests(unittest.TestCase):
    def setUp(self):
        self.source = _FakeSource()
        self.service = RelocalizationService(
            load_relocalization_config(), _CompleteMapRepository(), self.source,
        )
        self.service._socket = _FakeSocket()
        self.service.available = True

    def _response(self, request_id, message_type, state, sequence):
        envelope = RelocalizationEnvelope(
            "map-1", "UGV_001", "session-1", request_id, message_type,
            sequence, sequence, {"request_id": request_id, "state": state},
        )
        self.service.process_datagram(
            self.service.protocol.encode(envelope), "192.168.50.120",
        )

    def test_async_command_remains_retryable_until_terminal_status(self):
        snapshot = RelocalizationSnapshot(
            "map-1", "UGV_001", "session-1", RelocalizationStatus.MAP_TRANSFERRING,
            "downloading",
        )
        self.service._snapshots[("map-1", "ugv_001")] = snapshot
        self.service._queue(
            "map-1", "UGV_001", "session-1", "map_offer", {}, "192.168.50.120",
        )
        request_id = next(iter(self.service._pending))
        self.service._pending[request_id].attempts = self.service.config.command_max_attempts
        self._response(request_id, "download_status", "downloading", 1)
        self.assertIn(request_id, self.service._pending)
        self.assertEqual(self.service._pending[request_id].attempts, 0)
        self._response(request_id, "download_status", "verifying", 2)
        self.assertIn(request_id, self.service._pending)
        self._response(request_id, "download_status", "ready", 3)
        self.assertNotIn(request_id, self.service._pending)
        self.assertEqual(
            self.service.snapshot("map-1", "UGV_001").status,
            RelocalizationStatus.MAP_READY,
        )

    def test_invalid_inbound_transition_is_ignored(self):
        snapshot = RelocalizationSnapshot(
            "map-1", "UGV_001", "session-1", RelocalizationStatus.MAP_TRANSFERRING,
            "downloading",
        )
        self.service._snapshots[("map-1", "ugv_001")] = snapshot
        warnings = []
        self.service.protocol_warning.connect(warnings.append)
        self._response("request", "stack_status", "awaiting_pose", 1)
        self.assertEqual(
            self.service.snapshot("map-1", "UGV_001").status,
            RelocalizationStatus.MAP_TRANSFERRING,
        )
        self.assertTrue(warnings)

    def test_initial_pose_progress_keeps_request_pending_for_result_retry(self):
        snapshot = RelocalizationSnapshot(
            "map-1", "UGV_001", "session-1", RelocalizationStatus.RELOCALIZING,
            "relocalizing",
        )
        self.service._snapshots[("map-1", "ugv_001")] = snapshot
        self.service._queue(
            "map-1", "UGV_001", "session-1", "initial_pose", {}, "192.168.50.120",
        )
        request_id = next(iter(self.service._pending))
        self.service._pending[request_id].attempts = self.service.config.command_max_attempts
        self._response(request_id, "relocalization_result", "relocalizing", 1)
        self.assertIn(request_id, self.service._pending)
        self.assertEqual(self.service._pending[request_id].attempts, 0)
        self._response(request_id, "relocalization_result", "failed", 2)
        self.assertNotIn(request_id, self.service._pending)
        self.assertEqual(
            self.service.snapshot("map-1", "UGV_001").status,
            RelocalizationStatus.FAILED,
        )

    def test_stack_start_progress_resets_timeout_budget(self):
        snapshot = RelocalizationSnapshot(
            "map-1", "UGV_001", "session-1", RelocalizationStatus.STACK_STARTING,
            "starting",
        )
        self.service._snapshots[("map-1", "ugv_001")] = snapshot
        self.service._queue(
            "map-1", "UGV_001", "session-1", "start_stack", {}, "192.168.50.120",
        )
        request_id = next(iter(self.service._pending))
        pending = self.service._pending[request_id]
        pending.attempts = self.service.config.command_max_attempts
        self._response(request_id, "stack_status", "starting", 1)
        self.assertIn(request_id, self.service._pending)
        self.assertEqual(pending.attempts, 0)
        self._response(request_id, "stack_status", "awaiting_pose", 2)
        self.assertNotIn(request_id, self.service._pending)
        self.assertEqual(
            self.service.snapshot("map-1", "UGV_001").status,
            RelocalizationStatus.AWAITING_POSE,
        )

    def test_new_negotiation_discards_previous_device_requests(self):
        self.service._queue(
            "old-map", "UGV_001", "old-session", "negotiate", {}, "192.168.50.120",
        )
        self.service.negotiate("map-1", "UGV_001")
        pending = list(self.service._pending.values())
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].envelope.map_id, "map-1")

    def test_restarting_localized_device_invalidates_binding_and_marks_replace(self):
        binding = DeviceMapBinding(
            "map-1", "map", "odom", FrameTransform(1, 2, 0, 0, 0, 0, 1),
            datetime.now(timezone.utc), "vision_pose",
        )
        self.source.profile_item = replace(self.source.profile_item, map_bindings=(binding,))
        self.service._snapshots[("map-1", "ugv_001")] = RelocalizationSnapshot(
            "map-1", "UGV_001", "session-1", RelocalizationStatus.SUCCEEDED, "localized",
            can_start=True,
        )
        self.service.start_stack("map-1", "UGV_001")
        self.assertEqual(self.source.removed_bindings, [("UGV_001", "map-1")])
        sent = self.service.protocol.decode(self.service._socket.sent[-1][0])
        self.assertEqual(sent.message_type, "start_stack")
        self.assertTrue(sent.payload["replace_existing"])

    def test_localized_negotiation_restores_binding_from_edge_state(self):
        self.service._snapshots[("map-1", "ugv_001")] = RelocalizationSnapshot(
            "map-1", "UGV_001", "session-1", RelocalizationStatus.UNKNOWN_SPACE,
            "negotiating",
        )
        envelope = RelocalizationEnvelope(
            "map-1", "UGV_001", "session-1", "request", "negotiation_status",
            1, 1, {
                "request_id": "request", "state": "localized",
                "map_frame": "map", "odom_frame": "odom",
                "map_from_odom": {
                    "x": 3.0, "y": 4.0, "z": 0.0,
                    "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0,
                },
            },
        )
        self.service.process_datagram(
            self.service.protocol.encode(envelope), "192.168.50.120",
        )
        self.assertEqual(len(self.source.saved_bindings), 1)
        self.assertEqual(
            self.service.snapshot("map-1", "UGV_001").status,
            RelocalizationStatus.SUCCEEDED,
        )


try:
    from PySide6.QtWidgets import QApplication, QPlainTextEdit
    from ccs_monitor.pages.map_page import MapDetailPage, MapDeviceCard
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "Qt UI dependencies are unavailable")
class RelocalizationCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_buttons_and_retry_labels_follow_state_capabilities(self):
        card = MapDeviceCard(_FakeSource().device_item)
        card.update_snapshot(
            card.device, None,
            SimpleNamespace(
                status=RelocalizationStatus.UNKNOWN_SPACE, message="未知空间",
                can_download=True, can_start=False, can_submit_pose=False,
            ),
            map_complete=True,
        )
        self.assertTrue(card.download_map_button.isEnabled())
        self.assertFalse(card.relocalization_button.isEnabled())
        card.update_snapshot(
            card.device, None,
            SimpleNamespace(
                status=RelocalizationStatus.FAILED, message="失败",
                can_download=False, can_start=False, can_submit_pose=True,
            ),
            map_complete=True,
        )
        self.assertTrue(card.relocalization_button.isEnabled())
        self.assertEqual(card.relocalization_button.text(), "重新开始重定位")

    def test_protocol_log_scrolls_to_latest_line(self):
        widget = QPlainTextEdit()
        widget.resize(240, 80)
        widget.show()
        MapDetailPage._set_protocol_log(widget, [f"line {index}" for index in range(100)])
        self.app.processEvents()
        scrollbar = widget.verticalScrollBar()
        self.assertEqual(scrollbar.value(), scrollbar.maximum())
        widget.close()


if __name__ == "__main__":
    unittest.main()
