from __future__ import absolute_import

import hashlib
import io
import json
import os
import shutil
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import yaml


class ArtifactError(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with io.open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def safe_map_id(value):
    return (isinstance(value, str) and 1 <= len(value) <= 128
            and all(char.isalnum() or char in "-_" for char in value))


def _pcd_valid(path):
    fields = {}
    with io.open(path, "rb") as stream:
        for unused in range(128):
            line = stream.readline()
            if not line:
                break
            try:
                parts = line.decode("ascii").strip().split()
            except UnicodeDecodeError:
                return False
            if parts:
                fields[parts[0].upper()] = parts[1:]
            if parts and parts[0].upper() == "DATA":
                break
    try:
        return ({"x", "y", "z"}.issubset(set(fields["FIELDS"]))
                and int(fields["POINTS"][0]) > 0
                and fields["DATA"][0].lower() in ("ascii", "binary", "binary_compressed"))
    except (KeyError, IndexError, ValueError):
        return False


def validate_map_directory(path, map_id=None):
    root = os.path.abspath(path)
    expected = ("public_map.pcd", "map.pgm", "map.yaml")
    for name in expected:
        candidate = os.path.join(root, name)
        if os.path.islink(candidate) or not os.path.isfile(candidate) or os.path.getsize(candidate) <= 0:
            raise ArtifactError("map file is missing or invalid: %s" % name)
    if not _pcd_valid(os.path.join(root, "public_map.pcd")):
        raise ArtifactError("PCD header is invalid")
    with io.open(os.path.join(root, "map.pgm"), "rb") as stream:
        magic = stream.read(2)
    if magic not in (b"P2", b"P5"):
        raise ArtifactError("PGM header is invalid")
    try:
        with io.open(os.path.join(root, "map.yaml"), "r", encoding="utf-8") as stream:
            metadata = yaml.safe_load(stream)
    except (IOError, yaml.YAMLError) as exc:
        raise ArtifactError("map YAML is invalid: %s" % exc)
    if not isinstance(metadata, dict) or str(metadata.get("image", "")).replace("\\", "/") != "map.pgm":
        raise ArtifactError("map YAML does not reference map.pgm")
    return True


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ArtifactError("HTTP redirects are forbidden")


def download(url, destination, ground_ip, expected_size, expected_sha256,
             timeout_seconds, max_bytes):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != ground_ip or parsed.username or parsed.password:
        raise ArtifactError("map URL host or scheme is not allowed")
    offset = os.path.getsize(destination) if os.path.isfile(destination) else 0
    if offset > expected_size:
        os.unlink(destination)
        offset = 0
    headers = {"Range": "bytes=%d-" % offset} if offset else {}
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout_seconds)
        code = response.getcode()
        if offset and code != 206:
            response.close()
            os.unlink(destination)
            return download(url, destination, ground_ip, expected_size, expected_sha256,
                            timeout_seconds, max_bytes)
        mode = "ab" if offset else "wb"
        with response, io.open(destination, mode) as stream:
            total = offset
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes or total > expected_size:
                    raise ArtifactError("map download exceeds declared size")
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
    except (IOError, OSError, urllib.error.URLError) as exc:
        raise ArtifactError("map download failed: %s" % exc)
    if os.path.getsize(destination) != expected_size or sha256_file(destination) != expected_sha256:
        raise ArtifactError("map ZIP size or SHA-256 mismatch")


def install_archive(archive_path, map_root, map_id, max_bytes):
    if not safe_map_id(map_id):
        raise ArtifactError("map_id cannot be used as a directory")
    root = os.path.abspath(os.path.expanduser(map_root))
    os.makedirs(root, mode=0o750, exist_ok=True)
    incoming = os.path.join(root, ".incoming-%s" % map_id)
    target = os.path.join(root, map_id)
    backup = os.path.join(root, ".backup-%s" % map_id)
    if os.path.lexists(incoming):
        shutil.rmtree(incoming)
    os.makedirs(incoming, mode=0o750)
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            expected = {"manifest.json", "public_map.pcd", "map.pgm", "map.yaml"}
            if set(names) != expected or len(names) != len(expected):
                raise ArtifactError("map ZIP entries are incomplete or unexpected")
            total = 0
            for info in infos:
                mode = info.external_attr >> 16
                if (info.is_dir() or stat.S_ISLNK(mode) or info.file_size <= 0
                        or os.path.basename(info.filename) != info.filename):
                    raise ArtifactError("map ZIP contains an unsafe entry")
                total += info.file_size
            if total > max_bytes:
                raise ArtifactError("expanded map exceeds configured limit")
            archive.extractall(incoming)
        with io.open(os.path.join(incoming, "manifest.json"), "r", encoding="utf-8") as stream:
            manifest = json.load(stream)
        if manifest.get("schema_version") != 1 or manifest.get("map_id") != map_id:
            raise ArtifactError("map manifest identity is invalid")
        for role, name in (("pcd", "public_map.pcd"), ("pgm", "map.pgm"), ("yaml", "map.yaml")):
            item = manifest.get("files", {}).get(role, {})
            path = os.path.join(incoming, name)
            if (item.get("path") != name or item.get("byte_count") != os.path.getsize(path)
                    or item.get("sha256") != sha256_file(path)):
                raise ArtifactError("map manifest file check failed: %s" % role)
        os.unlink(os.path.join(incoming, "manifest.json"))
        validate_map_directory(incoming, map_id)
        if os.path.lexists(backup):
            shutil.rmtree(backup)
        if os.path.lexists(target):
            try:
                validate_map_directory(target, map_id)
            except ArtifactError:
                shutil.rmtree(target)
            else:
                os.replace(target, backup)
        os.replace(incoming, target)
        if os.path.lexists(backup):
            shutil.rmtree(backup)
        return target
    except Exception:
        if os.path.lexists(incoming):
            shutil.rmtree(incoming)
        if os.path.lexists(backup) and not os.path.lexists(target):
            os.replace(backup, target)
        raise
