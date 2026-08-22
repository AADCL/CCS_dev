import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile

import yaml

try:
    from ccs_monitor.map_building_config import load_map_building_config
    from ccs_monitor.map_building_v2 import ArtifactPackageValidator
except ModuleNotFoundError:
    load_map_building_config = None
    ArtifactPackageValidator = None

from epgeneral_map_stream.artifacts import (
    ArtifactError, ArtifactHttpServer, SessionPaths, build_archive, file_fingerprint,
    require_fresh_file,
    validate_artifacts, wait_for_stable_artifacts,
)
from epgeneral_map_stream.config import load_config

try:
    from .test_paths import device_config_path
except ImportError:
    from test_paths import device_config_path


PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPING = os.path.join(PACKAGE, "config", "mapping.yaml")
DEVICE = device_config_path(PACKAGE)


def write_outputs(paths):
    with io.open(paths.pcd_path, "w", encoding="ascii") as stream:
        stream.write("VERSION .7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n")
        stream.write("COUNT 1 1 1\nWIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n0 0 0\n")
    with io.open(paths.pgm_path, "wb") as stream:
        stream.write(b"P5\n2 2\n255\n" + bytes((0, 254, 205, 254)))
    with io.open(paths.yaml_path, "w", encoding="utf-8") as stream:
        yaml.safe_dump({
            "image": "map.pgm", "resolution": 0.1, "origin": [0.0, 0.0, 0.0],
            "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.196,
        }, stream)


class ArtifactTests(unittest.TestCase):
    def test_source_pcd_must_change_after_session_start(self):
        source = os.path.join(self.temp.name, "source.pcd")
        with io.open(source, "wb") as stream:
            stream.write(b"old")
        baseline = file_fingerprint(source)
        with self.assertRaisesRegex(ArtifactError, "not regenerated"):
            require_fresh_file(source, baseline, baseline["mtime_ns"])
        with io.open(source, "wb") as stream:
            stream.write(b"new mapping data")
        current = require_fresh_file(source, baseline, baseline["mtime_ns"])
        self.assertNotEqual(current["sha256"], baseline["sha256"])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = dict(load_config(MAPPING, DEVICE))
        self.config.update(
            workspace_root=self.temp.name, min_free_bytes=1,
            artifact_poll_seconds=0.001, artifact_stable_polls=2,
            artifact_generation_timeout_seconds=1.0,
        )
        self.identity = {"map_id": "map-1", "session_id": "b" * 32}
        self.paths = SessionPaths(self.config, self.identity)
        self.paths.prepare(1)

    @unittest.skipIf(ArtifactPackageValidator is None, "ground package is unavailable")
    def test_stable_files_build_exact_manifest_archive(self):
        write_outputs(self.paths)
        wait_for_stable_artifacts(self.paths, self.config)
        descriptor = build_archive(self.paths, self.config, self.identity)
        self.assertEqual(descriptor["byte_count"], os.path.getsize(self.paths.archive_path))
        with zipfile.ZipFile(self.paths.archive_path) as archive:
            self.assertEqual(set(archive.namelist()), {
                "manifest.json", "map.pcd", "map.pgm", "map.yaml"})
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        self.assertEqual(manifest["session_id"], self.identity["session_id"])
        self.assertEqual(set(manifest["files"]), {"pcd", "pgm", "yaml"})
        extracted = os.path.join(self.temp.name, "platform-validated")
        validated = ArtifactPackageValidator(load_map_building_config()).validate(
            __import__("pathlib").Path(self.paths.archive_path),
            __import__("pathlib").Path(extracted), map_id="map-1",
            device_id=self.config["device_id"], session_id=self.identity["session_id"])
        self.assertEqual(validated.frame_id, self.config["map_frame"])

    def test_invalid_yaml_and_symlink_are_rejected(self):
        write_outputs(self.paths)
        with io.open(self.paths.yaml_path, "w", encoding="utf-8") as stream:
            stream.write("image: wrong.pgm\n")
        with self.assertRaises(ArtifactError):
            validate_artifacts(self.paths, self.config["max_artifact_bytes"])

    def test_http_token_and_range(self):
        write_outputs(self.paths)
        build_archive(self.paths, self.config, self.identity)
        server = ArtifactHttpServer("127.0.0.1", 0)
        server.start()
        self.addCleanup(server.close)
        token, unused_expiry = server.register(self.paths.archive_path, 60)
        url = "http://127.0.0.1:%d/mapping/result.zip?token=%s" % (server.port, token)
        request = urllib.request.Request(url, headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(len(response.read()), 10)
            self.assertTrue(response.headers["Content-Range"].startswith("bytes 10-19/"))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(url.replace(token, "wrong"), timeout=2)
        self.assertEqual(caught.exception.code, 404)

    def test_invalid_range_returns_416(self):
        write_outputs(self.paths)
        build_archive(self.paths, self.config, self.identity)
        server = ArtifactHttpServer("127.0.0.1", 0)
        server.start()
        self.addCleanup(server.close)
        token, unused_expiry = server.register(self.paths.archive_path, 60)
        url = "http://127.0.0.1:%d/mapping/result.zip?token=%s" % (server.port, token)
        request = urllib.request.Request(url, headers={"Range": "bytes=999999-"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 416)
