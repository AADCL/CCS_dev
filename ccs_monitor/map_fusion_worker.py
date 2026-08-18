from __future__ import annotations

import importlib.util
import json
import sys
import traceback
import uuid
from pathlib import Path

from .map_fusion import voxel_merge


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    request_path, result_path = map(Path, sys.argv[1:])
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if request["script_path"] == "builtin:voxel_merge":
            result = voxel_merge(
                request["pcd_files"], request["transforms"], request["output_pcd"],
                float(request.get("options", {}).get("voxel_size_m", 0.1)),
            )
        elif request["script_path"] == "builtin:epgeneral_multi_map_fusion":
            from .epgeneral_multi_map_fusion import fuse_maps

            result = fuse_maps(
                request["pcd_files"], request["primary_frame"], request["transforms"],
                request["output_pcd"], request.get("options", {}),
            )
        else:
            script = Path(request["script_path"])
            spec = importlib.util.spec_from_file_location(f"ccs_fusion_{uuid.uuid4().hex}", script)
            if spec is None or spec.loader is None:
                raise RuntimeError("无法加载融合算法")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = module.fuse_maps(
                request["pcd_files"], request["primary_frame"], request["transforms"],
                request["output_pcd"], request.get("options", {}),
            )
        if not isinstance(result, dict):
            raise TypeError("fuse_maps 返回值必须为 dict")
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
