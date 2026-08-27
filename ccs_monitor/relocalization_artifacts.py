from __future__ import annotations

import hashlib
import hmac
import json
import os
import socketserver
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import yaml


class RelocalizationArtifactError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_relocalization_archive(repository, map_id: str, max_bytes: int) -> tuple[Path, dict]:
    try:
        pcd = repository.pcd_path(map_id)
        map_yaml, pgm = repository.pgm_paths(map_id)
    except Exception as exc:
        raise RelocalizationArtifactError(f"地图缺少完整 PCD/PGM/YAML：{exc}") from exc
    files = {"pcd": ("public_map.pcd", pcd), "pgm": ("map.pgm", pgm), "yaml": ("map.yaml", map_yaml)}
    total = sum(path.stat().st_size for _, path in files.values())
    if total <= 0 or total > max_bytes:
        raise RelocalizationArtifactError("地图产物大小超出限制")
    try:
        metadata = yaml.safe_load(map_yaml.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RelocalizationArtifactError(f"地图 YAML 无效：{exc}") from exc
    if not isinstance(metadata, dict) or str(metadata.get("image", "")).replace("\\", "/") != "map.pgm":
        raise RelocalizationArtifactError("地图 YAML 未引用对应 PGM")
    manifest_files = {
        role: {"path": name, "byte_count": path.stat().st_size, "sha256": sha256_file(path)}
        for role, (name, path) in files.items()
    }
    manifest = {
        "schema_version": 1, "map_id": map_id, "frame_id": "map",
        "generated_at": datetime.now(timezone.utc).isoformat(), "files": manifest_files,
    }
    handle = tempfile.NamedTemporaryFile(prefix="ccs-relocalization-", suffix=".zip", delete=False)
    archive_path = Path(handle.name)
    handle.close()
    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True).encode("utf-8"))
            for _, (name, path) in files.items():
                archive.write(path, name)
        size = archive_path.stat().st_size
        if size <= 0 or size > max_bytes:
            raise RelocalizationArtifactError("重定位 ZIP 大小超出限制")
        descriptor = {"byte_count": size, "sha256": sha256_file(archive_path), "manifest": manifest}
        return archive_path, descriptor
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


class _ThreadedHttpServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class RelocalizationHttpServer:
    def __init__(self, bind_host: str, port: int, clock=time.time) -> None:
        self.clock = clock
        self._entries: dict[str, dict] = {}
        self._lock = threading.RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner._serve(self)

            def do_HEAD(self):
                owner._serve(self, head_only=True)

            def log_message(self, _format, *_args):
                return

        self.server = _ThreadedHttpServer((bind_host, port), Handler)
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self.server.serve_forever, name="relocalization-http", daemon=True)
        self.thread.start()

    def register(self, path: Path, ttl_seconds: float) -> tuple[str, str]:
        token = uuid.uuid4().hex
        expires = self.clock() + ttl_seconds
        with self._lock:
            self._entries[token] = {"path": path, "expires": expires}
        return token, datetime.fromtimestamp(expires, timezone.utc).isoformat()

    def unregister(self, token: str, delete: bool = True) -> None:
        with self._lock:
            entry = self._entries.pop(token, None)
        if delete and entry:
            Path(entry["path"]).unlink(missing_ok=True)

    def cleanup(self) -> None:
        with self._lock:
            expired = [token for token, item in self._entries.items() if item["expires"] <= self.clock()]
        for token in expired:
            self.unregister(token)

    def _serve(self, handler, head_only: bool = False) -> None:
        parsed = urlsplit(handler.path)
        tokens = parse_qs(parsed.query).get("token", [])
        entry = None
        if parsed.path == "/relocalization/map.zip" and len(tokens) == 1:
            with self._lock:
                for token, candidate in self._entries.items():
                    if hmac.compare_digest(token, tokens[0]) and candidate["expires"] > self.clock():
                        entry = candidate
                        break
        path = Path(entry["path"]) if entry else None
        if path is None or not path.is_file() or path.is_symlink():
            handler.send_error(404)
            return
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        header = handler.headers.get("Range")
        if header:
            try:
                if not header.startswith("bytes=") or "," in header:
                    raise ValueError
                first, last = header[6:].split("-", 1)
                if first:
                    start, end = int(first), int(last) if last else size - 1
                else:
                    length = int(last)
                    if length <= 0:
                        raise ValueError
                    start = max(0, size - length)
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end, partial = min(end, size - 1), True
            except (TypeError, ValueError):
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.end_headers()
                return
        length = end - start + 1
        handler.send_response(206 if partial else 200)
        handler.send_header("Content-Type", "application/zip")
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", str(length))
        if partial:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.end_headers()
        if head_only:
            return
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)

    def close(self) -> None:
        with self._lock:
            tokens = list(self._entries)
        for token in tokens:
            self.unregister(token)
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.thread = None
