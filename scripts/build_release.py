"""Local release builder. Does not publish or read live config/data."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ccs_monitor.version import __version__ as VERSION
from scripts.release_documentation import stage_documentation

EDGE_PACKAGES = (
    "EPGeneral_device_config", "EPGeneral_map_stream", "epgeneral_mqtav",
    "EPGeneral_relocalization", "EPGeneral_ground_air_control",
    "EPGeneral_task_control",
    "EPGeneral_udp_telemetry", "EPGeneral_video_srt",
)
EXCLUDED_PARTS = {"__pycache__", ".git", ".venv", ".trash", ".pytest_cache", "build", "devel", "logs"}
DOCUMENTS = ("README.md", "CHANGELOG.md", "LICENSE", "需求分析.md")
PORTABLE_FILES = ("run.py", "pyproject.toml", "uv.lock", ".python-version", "requirements.txt")

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

def copy_tree(source: Path, destination: Path, suffixes: set[str] | None = None) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in EXCLUDED_PARTS or part.startswith(".") for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Release source must not contain symlinks: {path}")
        if path.is_file() and (suffixes is None or path.suffix.lower() in suffixes):
            copy_file(path, destination / relative)

def seed_defaults(destination: Path, source_root: Path = ROOT) -> None:
    copy_tree(source_root / "release/defaults", destination)
    devices = json.loads((destination / "config/devices.json").read_text(encoding="utf-8"))
    if devices.get("devices") != []:
        raise ValueError("Release defaults must have an empty device list")
    types = json.loads((destination / "config/device_types.json").read_text(encoding="utf-8"))
    for item in types["device_types"]:
        relative = PurePosixPath(item["icon_path"])
        if relative.is_absolute() or ".." in relative.parts or not (destination / relative).is_file():
            raise ValueError(f"Missing or invalid default type icon: {relative}")

def documentation(destination: Path) -> None:
    stage_documentation(ROOT, destination, VERSION)
    copy_file(ROOT / "release/THIRD_PARTY_NOTICES.md", destination / "THIRD_PARTY_NOTICES.md")

def inventory(destination: Path, kind: str, extra: dict | None = None) -> None:
    files = sorted(p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file() and p.name not in ("release-manifest.json", ".ccs-install.json"))
    record = {
        "schema_version": 1, "product": "CCS", "version": VERSION, "kind": kind,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "files": files, "sha256": {f: sha256(destination / f) for f in files},
        **(extra or {}),
    }
    write_json(destination / "release-manifest.json", record)
    if kind == "installer":
        record["files"] = files + ["release-manifest.json"]
        record["sha256"]["release-manifest.json"] = sha256(destination / "release-manifest.json")
        write_json(destination / ".ccs-install.json", record)

def archive_zip(source: Path, target: Path) -> Path:
    temporary = target.with_suffix(target.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = f"{source.name}/{path.relative_to(source).as_posix()}"
            info = zipfile.ZipInfo(relative, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if path.suffix == ".sh" else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    os.replace(temporary, target)
    return target

def portable(work: Path, output: Path) -> Path:
    destination = work / f"CCS-{VERSION}"
    destination.mkdir()
    for filename in PORTABLE_FILES:
        copy_file(ROOT / filename, destination / filename)
    copy_tree(ROOT / "ccs_monitor", destination / "ccs_monitor", {".py", ".svg", ".png"})
    for directory in ("icons/app_icons", "icons/lab_logo"):
        copy_tree(ROOT / directory, destination / directory, {".svg", ".png"})
    copy_tree(ROOT / "examples", destination / "examples", {".py"})
    for filename in ("setup_env.ps1", "setup_env.sh"):
        copy_file(ROOT / "scripts" / filename, destination / "scripts" / filename)
    seed_defaults(destination)
    documentation(destination)
    inventory(destination, "portable")
    return archive_zip(destination, output / f"CCS-{VERSION}-portable.zip")

def edge(work: Path, output: Path) -> Path:
    destination = work / f"CCS-{VERSION}-edge"
    destination.mkdir()
    for directory in (*EDGE_PACKAGES, "deploy", "documents"):
        source = ROOT / "edge_side_pkg" / directory
        if not source.is_dir():
            raise ValueError(f"Missing edge package directory: {directory}")
        copy_tree(source, destination / "edge_side_pkg" / directory,
                  {".py", ".sh", ".md", ".txt", ".xml", ".yaml", ".yml", ".launch", ".msg", ".srv", ".cfg", ".conf", ".json", ".rviz", ".rules", ".cpp", ".hpp", ".h", ".cmake", ".service", ".patch"})
    copy_file(ROOT / "edge_side_pkg/README.md", destination / "edge_side_pkg/README.md")
    copy_file(ROOT / "LICENSE", destination / "LICENSE")
    copy_file(ROOT / "release/THIRD_PARTY_NOTICES.md", destination / "THIRD_PARTY_NOTICES.md")
    copy_file(ROOT / "docs/EDGE_DEVICE_INTERFACES.md", destination / "docs/EDGE_DEVICE_INTERFACES.md")
    stage_documentation(ROOT, destination, VERSION, edge=True)
    inventory(destination, "edge")
    return archive_zip(destination, output / f"CCS-{VERSION}-edge.zip")

def download_verified(entry: dict, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / entry["url"].rsplit("/", 1)[-1]
    if not target.exists() or sha256(target) != entry["sha256"]:
        partial = target.with_suffix(target.suffix + ".part")
        request = urllib.request.Request(entry["url"], headers={"User-Agent": "CCS-release-builder"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if sha256(partial) != entry["sha256"]:
            partial.unlink()
            raise ValueError(f"SHA-256 mismatch: {entry['url']}")
        os.replace(partial, target)
    return target

def unpack_verified(archive: Path, target: Path) -> None:
    target.mkdir()
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as stream:
            for member in stream.infolist():
                parts = PurePosixPath(member.filename)
                if parts.is_absolute() or ".." in parts.parts or "\\" in member.filename or ":" in member.filename:
                    raise ValueError("Unsafe archive path")
            stream.extractall(target)
    else:
        with tarfile.open(archive) as stream:
            for member in stream.getmembers():
                parts = PurePosixPath(member.name)
                if parts.is_absolute() or ".." in parts.parts or not (member.isfile() or member.isdir()):
                    raise ValueError("Unsafe archive member")
            stream.extractall(target)

def bundle_ffmpeg(destination: Path, work: Path, system: str, supplied: Path | None) -> dict:
    entry = json.loads((ROOT / "release/ffmpeg.lock.json").read_text())[system]
    archive = supplied or download_verified(entry, ROOT / "build/downloads")
    if sha256(archive) != entry["sha256"]:
        raise ValueError("FFmpeg archive must match release/ffmpeg.lock.json")
    unpacked = work / "ffmpeg"
    unpack_verified(archive, unpacked)
    executable_name = "ffmpeg.exe" if system == "windows" else "ffmpeg"
    executables = list(unpacked.rglob(executable_name))
    if len(executables) != 1:
        raise ValueError("Expected one FFmpeg executable")
    executable = executables[0]
    executable.chmod(0o755)
    protocols = subprocess.check_output([str(executable), "-hide_banner", "-protocols"], text=True)
    decoders = subprocess.check_output([str(executable), "-hide_banner", "-decoders"], text=True)
    if not re.search(r"(?m)^\s+srt\s*$", protocols.split("Output:")[0]) or not re.search(r"\bh264\b", decoders):
        raise ValueError("Bundled FFmpeg must support SRT input and H.264 decoding")
    target = destination / "tools/ffmpeg"
    copy_file(executable, target / "bin" / executable_name)
    for library in executable.parent.glob("*.dll"):
        copy_file(library, target / "bin" / library.name)
    licenses = [p for p in unpacked.rglob("*") if p.is_file() and any(token in p.name.lower() for token in ("license", "copying", "readme"))]
    if not licenses:
        raise ValueError("FFmpeg distribution is missing licensing information")
    for i, path in enumerate(licenses):
        copy_file(path, target / "licenses" / f"{i}-{path.name}")
    write_json(target / "build-input.json", entry)
    return entry

def python_licenses(destination: Path) -> None:
    import sysconfig
    for candidate in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE",
                      Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"):
        if candidate.is_file():
            copy_file(candidate, destination / "licenses/CPython-LICENSE.txt")
            break
    records = []
    for package in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = package.metadata["Name"]
        records.append({"name": name, "version": package.version,
                        "license": package.metadata.get("License-Expression") or package.metadata.get("License") or "See package license files"})
        for relative in package.files or []:
            if any(token in relative.name.lower() for token in ("license", "copying")):
                source = Path(package.locate_file(relative))
                if source.is_file():
                    copy_file(source, destination / "licenses/python" / name / str(relative).replace("..", "_"))
    write_json(destination / "licenses/python-packages.json", records)

def app_icon(work: Path) -> Path:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import Qt
    from PIL import Image
    app = QApplication.instance() or QApplication([])
    picture = QImage(256, 256, QImage.Format.Format_ARGB32)
    picture.fill(Qt.GlobalColor.transparent)
    painter = QPainter(picture)
    QSvgRenderer(str(ROOT / "ccs_monitor/assets/ccs_logo.svg")).render(painter)
    painter.end()
    png = work / "ccs.png"
    picture.save(str(png))
    target = work / "ccs.ico"
    Image.open(png).save(target, sizes=[(16,16), (32,32), (48,48), (64,64), (256,256)])
    return target

def installer(work: Path, output: Path, args) -> Path:
    if platform.machine().lower() not in ("amd64", "x86_64") or sys.platform not in ("win32", "linux"):
        raise ValueError("Installer builds require native Windows/Linux x64")
    if sys.version_info[:2] != (3, 10):
        raise ValueError("Installer build baseline is Python 3.10. Run the release wrapper.")
    for name, expected in (("PySide6", "6.8.3"), ("pyinstaller", "6.22.2")):
        if importlib.metadata.version(name) != expected:
            raise ValueError(f"{name} must be {expected}; rebuild the locked release environment")
    system = "windows" if sys.platform == "win32" else "linux"
    if system == "linux":
        info = platform.freedesktop_os_release()
        if info.get("ID") != "ubuntu" or info.get("VERSION_ID") != "20.04":
            raise ValueError("Build Linux installers inside the supplied Ubuntu 20.04 container")
    ffmpeg_entry = json.loads((ROOT / "release/ffmpeg.lock.json").read_text())[system]
    ffmpeg_archive = args.ffmpeg_archive or download_verified(ffmpeg_entry, ROOT / "build/downloads")
    if sha256(ffmpeg_archive) != ffmpeg_entry["sha256"]:
        raise ValueError("FFmpeg archive checksum mismatch")
    icon = app_icon(work)
    env = dict(os.environ, CCS_APP_ICON=str(icon))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(work / "frozen"), "--workpath", str(work / "pyinstaller"),
                    str(ROOT / "release/ccs.spec")], cwd=ROOT, env=env, check=True)
    destination = work / "frozen/CCS"
    seed_defaults(destination)
    documentation(destination)
    copy_tree(ROOT / "examples", destination / "examples", {".py"})
    ffmpeg = bundle_ffmpeg(destination, work, system, ffmpeg_archive)
    python_licenses(destination)
    if system == "linux":
        copy_file(ROOT / "release/uninstall.sh", destination / "uninstall.sh")
        (destination / "uninstall.sh").chmod(0o755)
    inventory(destination, "installer", {"platform": system + "-x64", "ffmpeg": ffmpeg})
    if system == "windows":
        compiler = args.iscc or shutil.which("ISCC") or next((str(p) for p in (
            ROOT / "build/tools/InnoSetup/ISCC.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe",
        ) if p.is_file()), None)
        if not compiler:
            raise ValueError("Install Inno Setup 6 or pass --iscc PATH (see docs/RELEASING.md)")
        subprocess.run([str(compiler), f"/DAppVersion={VERSION}", f"/DSourceDir={destination}",
                        f"/DOutputDir={output}", f"/DAppIcon={icon}", str(ROOT / "release/windows.iss")], check=True)
        return output / f"CCS-{VERSION}-windows-x64-setup.exe"
    payload = work / "payload.tar.gz"
    with tarfile.open(payload, "w:gz", dereference=True) as archive:
        for child in sorted(destination.iterdir()):
            archive.add(child, arcname=child.name)
    header = (ROOT / "release/linux-installer.sh.in").read_text().replace("@VERSION@", VERSION).replace("@SHA256@", sha256(payload))
    target = output / f"CCS-{VERSION}-linux-x64.run"
    with target.open("wb") as stream, payload.open("rb") as source:
        stream.write(header.encode())
        shutil.copyfileobj(source, stream)
    target.chmod(0o755)
    return target

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("all", "installer", "portable", "edge"), default="all")
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--ffmpeg-archive", type=Path, help="Offline cache; must match ffmpeg.lock.json")
    args = parser.parse_args()
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    (ROOT / "build").mkdir(exist_ok=True)
    # Keep work dirs for diagnosis; never recursively delete arbitrary user paths.
    work = Path(tempfile.mkdtemp(prefix="release-", dir=ROOT / "build"))
    produced = []
    try:
        if args.target in ("all", "portable"):
            produced.append(portable(work, output))
        if args.target in ("all", "edge"):
            produced.append(edge(work, output))
        if args.target in ("all", "installer"):
            produced.append(installer(work, output, args))
    finally:
        for path in produced:
            path.with_name(path.name + ".sha256").write_text(f"{sha256(path)}  {path.name}\n", encoding="utf-8")
            print(path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
