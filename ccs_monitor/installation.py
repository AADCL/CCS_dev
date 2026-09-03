"""Manifest-based Linux installation; never remove user config or data."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

MARKER = ".ccs-install.json"

def managed_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str):
        raise ValueError("Installation paths must be strings")
    part = PurePosixPath(relative)
    if not relative or part.is_absolute() or ".." in part.parts or "\\" in relative or ":" in relative:
        raise ValueError(f"Invalid installation path: {relative!r}")
    target = root.joinpath(*part.parts)
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != root.parent):
        raise ValueError(f"Symbolic link in installation path: {target}")
    target.resolve().relative_to(root.resolve())
    return target

def mutable(relative: str) -> bool:
    return PurePosixPath(relative).parts[0] in ("config", "data") or relative == ".ccs-running.lock"

def read_manifest(root: Path) -> dict:
    manifest = json.loads((root / MARKER).read_text(encoding="utf-8"))
    if manifest.get("product") != "CCS" or manifest.get("schema_version") != 1:
        raise ValueError("Not a CCS installation manifest")
    if not isinstance(manifest.get("files"), list):
        raise ValueError("Invalid file inventory")
    for relative in manifest["files"]:
        managed_path(root, relative)
    return manifest

@contextmanager
def installation_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / ".ccs-running.lock").open("a+b")
    try:
        if os.name == "posix":
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("CCS is running. Close it before installing or uninstalling.") from exc
        yield
    finally:
        handle.close()

def install_tree(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.absolute()
    if destination.is_symlink() or destination.resolve() in (Path(destination.anchor), Path.home().resolve()):
        raise ValueError("Choose a dedicated application directory")
    destination = destination.resolve()
    if any(c in str(destination) for c in "\n\r"):
        raise ValueError("Newlines are not supported in installation directories")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must be separate directories")
    new = read_manifest(source)
    old = read_manifest(destination) if (destination / MARKER).is_file() else None
    if old is None and destination.exists() and any(destination.iterdir()):
        raise ValueError("Destination is not empty and is not a CCS installation")
    for relative in new["files"]:
        path = managed_path(source, relative)
        if not path.is_file():
            raise ValueError(f"Missing payload: {relative}")
        expected = new.get("sha256", {}).get(relative)
        if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Corrupt payload: {relative}")
        target = managed_path(destination, relative)
        if target.exists() and not target.is_file():
            raise ValueError(f"A directory blocks an application file: {target}")
    with installation_lock(destination), tempfile.TemporaryDirectory(prefix="ccs-backup-", dir=destination.parent) as temp:
        backup = Path(temp)
        backed_up, written = [], []
        try:
            old_files = set(old["files"] if old else [])
            new_files = set(new["files"])
            for relative in sorted(old_files | new_files | {MARKER}):
                target = managed_path(destination, relative)
                if target.is_file() and not mutable(relative):
                    saved = managed_path(backup, relative)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, saved)
                    backed_up.append(relative)
            for relative in new["files"]:
                target = managed_path(destination, relative)
                if mutable(relative) and target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                written.append(relative)
                shutil.copy2(managed_path(source, relative), target)
            written.append(MARKER)
            shutil.copy2(source / MARKER, destination / MARKER)
        except BaseException:
            for relative in reversed(written):
                managed_path(destination, relative).unlink(missing_ok=True)
            for relative in reversed(backed_up):
                target = managed_path(destination, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(managed_path(backup, relative), target)
            raise

def desktop_file(root: Path) -> Path:
    suffix = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "applications" / f"ccs-{suffix}.desktop"

def install_desktop(root: Path) -> None:
    def quote(value: str) -> str:
        value = value.replace("\\", "\\\\\\\\").replace("%", "%%")
        for char in (chr(34), chr(96), chr(36)):
            value = value.replace(char, "\\\\" + char)
        return '"' + value + '"'
    if any(c in str(root) for c in "\n\r"):
        raise ValueError("Newlines are not supported in installation directories")
    target = desktop_file(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[Desktop Entry]\nType=Application\nName=CCS 指控平台\n"
                      f"Exec={quote(str(root / 'CCS'))}\n"
                      f"Icon={root / '_internal/ccs_monitor/assets/ccs_logo.svg'}\n"
                      "Terminal=false\nCategories=Science;Education;\n", encoding="utf-8")

def uninstall_tree(root: Path) -> None:
    root = root.resolve()
    manifest = read_manifest(root)
    with installation_lock(root):
        for relative in manifest["files"]:
            if not mutable(relative):
                managed_path(root, relative).unlink(missing_ok=True)
        (root / MARKER).write_text(json.dumps({
            "product": "CCS", "schema_version": 1, "version": manifest.get("version"),
            "state": "uninstalled", "files": [], "sha256": {},
        }), encoding="utf-8")
        desktop_file(root).unlink(missing_ok=True)
        for directory in sorted((p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: len(p.parts), reverse=True):
            if directory.relative_to(root).parts[0] in ("config", "data"):
                continue
            try:
                directory.rmdir()
            except OSError:
                pass

def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", type=Path)
    group.add_argument("--uninstall", action="store_true")
    parser.add_argument("--prefix", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.install:
            install_tree(args.install, args.prefix)
            install_desktop(args.prefix.resolve())
            print(f"Installed CCS in {args.prefix.resolve()}")
        else:
            uninstall_tree(args.prefix)
            print("CCS removed. User config/ and data/ were retained.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Installation failed: {exc}", file=__import__("sys").stderr)
        return 1
