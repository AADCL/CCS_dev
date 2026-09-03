"""Prepare black-box inputs for a Python-free, network-disabled runtime container."""
import argparse
import json
from pathlib import Path
import shutil
from smoke_release import PROBE

ROOT = Path(__file__).resolve().parents[1]
APP = "/tmp/ccs-user/安装 CCS"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/linux-validation")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "probe.py").write_text(PROBE, encoding="utf-8")
    shutil.copy2(ROOT / "tests/fixtures/frozen_gui_probe.py", args.output / "gui.py")
    shutil.copy2(ROOT / "tests/fixtures/srt_h264.ts", args.output / "srt_h264.ts")
    shutil.copy2(ROOT / "tests/test_linux_installer.sh", args.output / "test_linux_installer.sh")
    points = [(x / 10, y / 10, (x * y % 7) / 20) for x in range(10) for y in range(10)]
    (args.output / "input.pcd").write_text("# .PCD v0.7\nVERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH 100\nHEIGHT 1\nPOINTS 100\nDATA ascii\n" +
        "\n".join(" ".join(map(str, p)) for p in points) + "\n")
    for name, script in (
        ("probe", "/validation/probe.py"), ("gui", "/validation/gui.py"),
        ("builtin", "builtin:voxel_merge"), ("numpy", APP + "/examples/map_fusion_plugin_example.py"),
        ("ransac", APP + "/examples/map_fusion_ransac.py"), ("open3d", APP + "/examples/map_fusion_open3d_icp.py"),
    ):
        request = {"script_path": script, "pcd_files": ["/validation/input.pcd"] * 2, "primary_frame": "map",
                   "output_pcd": f"/tmp/ccs-user/{name}.pcd", "options": {},
                   "transforms": [{"map_id": key, "is_primary": i == 0, "translation_m": [0,0,0],
                                   "rotation_rpy_deg": [0,0,0]} for i, key in enumerate(("a","b"))]}
        (args.output / f"{name}.json").write_text(json.dumps(request), encoding="utf-8")
    print(args.output)

if __name__ == "__main__":
    main()
