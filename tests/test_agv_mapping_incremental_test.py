import hashlib
import io
import json
import struct
import unittest
import zipfile

from scripts.agv_mapping_incremental_test import (
    validate_artifact,
    validate_fragment_contract,
    validate_fragment_content,
    validate_prepare_contract,
)


class AgvMappingIncrementalTestTests(unittest.TestCase):
    def test_frame_contract_accepts_odom_preview_from_camera_init(self):
        validate_prepare_contract({"frame_id": "odom"})
        validate_fragment_contract({
            "frame_id": "odom",
            "source_frame_id": "camera_init",
            "display_from_source": {
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "qw": 1.0,
            },
        })

    def test_frame_contract_rejects_old_or_incomplete_values(self):
        with self.assertRaisesRegex(RuntimeError, "prepare frame mismatch"):
            validate_prepare_contract({"frame_id": "camera_init"})
        with self.assertRaisesRegex(RuntimeError, "fragment frame mismatch"):
            validate_fragment_contract({
                "frame_id": "camera_init",
                "source_frame_id": "camera_init",
                "display_from_source": {},
            })
        with self.assertRaisesRegex(RuntimeError, "display_from_source"):
            validate_fragment_contract({
                "frame_id": "odom",
                "source_frame_id": "camera_init",
                "display_from_source": {"x": 0.0},
            })

    def test_artifact_requires_map_frame_and_verified_pgm_yaml_pair(self):
        content = self._artifact(frame_id="map")
        manifest = validate_artifact(content)
        self.assertEqual(manifest["frame_id"], "map")

        with self.assertRaisesRegex(RuntimeError, "artifact frame mismatch"):
            validate_artifact(self._artifact(frame_id="camera_init"))

    def test_artifact_rejects_manifest_checksum_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, "pgm SHA-256 mismatch"):
            validate_artifact(self._artifact(frame_id="map", bad_pgm_hash=True))

    def test_fragment_content_requires_verified_binary_xyz_pcd(self):
        binary = struct.pack("<ffffff", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        content = (
            b"# .PCD v0.7\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\n"
            b"TYPE F F F\nCOUNT 1 1 1\nWIDTH 2\nHEIGHT 1\n"
            b"POINTS 2\nDATA binary\n" + binary
        )
        payload = {
            "byte_count": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "point_count": 2,
        }
        self.assertEqual(
            validate_fragment_content(payload, content)["point_count"], 2
        )

        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            validate_fragment_content({**payload, "sha256": "0" * 64}, content)
        with self.assertRaisesRegex(RuntimeError, "point count"):
            validate_fragment_content({**payload, "point_count": 3}, content)

    @staticmethod
    def _artifact(*, frame_id, bad_pgm_hash=False):
        files = {
            "map.pgm": b"P5\n1 1\n255\n\x00",
            "map.yaml": b"image: map.pgm\nresolution: 0.05\n",
        }
        manifest_files = {}
        for kind, path in (("pgm", "map.pgm"), ("yaml", "map.yaml")):
            digest = hashlib.sha256(files[path]).hexdigest()
            if kind == "pgm" and bad_pgm_hash:
                digest = "0" * 64
            manifest_files[kind] = {
                "path": path,
                "byte_count": len(files[path]),
                "sha256": digest,
            }
        manifest = {
            "frame_id": frame_id,
            "files": manifest_files,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for path, data in files.items():
                archive.writestr(path, data)
        return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()
