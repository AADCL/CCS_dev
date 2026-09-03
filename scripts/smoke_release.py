"""Exercise an actual frozen distribution using only a stdlib test driver."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

PROBE = """
def fuse_maps(pcd_files, primary_frame, transforms, output_pcd, options):
    import asyncio
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from ccs_monitor.runtime_paths import application_root, resource_root
    from ccs_monitor.app_icons import icon_path, lab_logo_path
    from ccs_monitor.styles import ThemeMode
    from ccs_monitor.map_fusion import voxel_merge
    from amqtt.broker import Broker
    import vispy.app
    import open3d
    app = QApplication.instance() or QApplication([])
    for path in (icon_path("mapStorage", ThemeMode.DAY), icon_path("mapStorage", ThemeMode.NIGHT), lab_logo_path()):
        assert path.is_file(), str(path)
        assert not QIcon(str(path)).pixmap(32, 32).isNull(), str(path)
    vispy.app.use_app("pyside6")
    async def broker_test():
        broker = Broker({"listeners": {"default": {"type": "tcp", "bind": "127.0.0.1:0"}},
                         "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
        await broker.start()
        await broker.shutdown()
    asyncio.run(broker_test())
    result = voxel_merge(pcd_files, transforms, output_pcd, 0.01)
    result.update({"icons": True, "mqtt": True, "vispy_backend": True,
                   "open3d_version": open3d.__version__, "application_root": str(application_root()),
                   "resource_root": str(resource_root())})
    return result
"""

def check_worker(root: Path, work: Path) -> dict:
    executable = root / ("ccs-map-fusion-worker.exe" if os.name == "nt" else "ccs-map-fusion-worker")
    points = [(x / 10, y / 10, (x * y % 7) / 20) for x in range(10) for y in range(10)]
    source = work / "input.pcd"
    source.write_text("# .PCD v0.7\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH 100\nHEIGHT 1\nPOINTS 100\nDATA ascii\n" +
                      "\n".join(" ".join(map(str, p)) for p in points) + "\n")
    probe = work / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    transforms = [{"map_id": name, "is_primary": index == 0, "translation_m": [0,0,0], "rotation_rpy_deg": [0,0,0]}
                  for index, name in enumerate(("a", "b"))]
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    environment.pop("PYTHONPATH", None)
    # Worker must use bundled Python and libraries, not host Python/FFmpeg.
    environment["PATH"] = os.environ.get("SystemRoot", "/usr") + ("/System32" if os.name == "nt" else "/bin")
    results = {}
    scripts = [("probe", str(probe)),
               ("gui", str(Path(__file__).resolve().parents[1] / "tests/fixtures/frozen_gui_probe.py")), ("builtin", "builtin:voxel_merge"),
               ("numpy", str(root / "examples/map_fusion_plugin_example.py")),
               ("ransac", str(root / "examples/map_fusion_ransac.py")),
               ("open3d", str(root / "examples/map_fusion_open3d_icp.py"))]
    for name, script in scripts:
        output = work / f"{name}.pcd"
        request, result = work / "request.json", work / "result.json"
        request.write_text(json.dumps({"script_path": script, "pcd_files": [str(source)] * 2,
                                      "primary_frame": "map", "transforms": transforms,
                                      "output_pcd": str(output), "options": {}}), encoding="utf-8")
        completed = subprocess.run([str(executable), str(request), str(result)], cwd=work, env=environment,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if completed.returncode:
            raise RuntimeError(f"{name}: {completed.stderr[-6000:]}")
        if not output.is_file():
            raise RuntimeError(f"{name}: no PCD output")
        results[name] = json.loads(result.read_text(encoding="utf-8"))
    bad = work / "bad.py"
    bad.write_text("def fuse_maps(*args):\n    raise RuntimeError('expected worker failure')\n")
    data = json.loads(request.read_text())
    data["script_path"] = str(bad)
    request.write_text(json.dumps(data))
    completed = subprocess.run([str(executable), str(request), str(result)], env=environment, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert completed.returncode != 0 and "expected worker failure" in completed.stderr
    results["worker_error"] = True
    return results

def check_srt(root: Path, fixture: Path) -> dict:
    executable = root / "tools/ffmpeg/bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    listener = f"srt://127.0.0.1:{port}?mode=listener&transtype=live&latency=120000"
    caller = f"srt://127.0.0.1:{port}?mode=caller&transtype=live&latency=120000"
    sender = subprocess.Popen([str(executable), "-hide_banner", "-loglevel", "error", "-re", "-stream_loop", "-1",
                               "-i", str(fixture), "-c", "copy", "-f", "mpegts", listener],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        receiver = subprocess.run([str(executable), "-hide_banner", "-loglevel", "error", "-probesize", "32768",
                                   "-analyzeduration", "200000", "-i", caller, "-frames:v", "1",
                                   "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, timeout=20)
        if receiver.returncode or len(receiver.stdout) != 64 * 48 * 3:
            raise RuntimeError(f"SRT decode failed: {receiver.stderr.decode(errors='replace')}")
        return {"srt_h264_frame_bytes": len(receiver.stdout)}
    finally:
        sender.terminate()
        try:
            sender.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sender.kill()
            sender.wait()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).resolve().parents[1] / "tests/fixtures/srt_h264.ts")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="CCS smoke 中文 ") as directory:
        result = check_worker(args.directory.resolve(), Path(directory).resolve())
        result.update(check_srt(args.directory.resolve(), args.fixture.resolve()))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
