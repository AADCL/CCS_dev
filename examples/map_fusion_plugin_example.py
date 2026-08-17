"""CCS 地图融合插件示例。

将本文件复制后修改常量和 ``fuse_maps``，再从地图页的“融合算法”对话框导入。
插件在独立进程运行，不得引用 Qt 控件或修改地面站数据目录。每个输入 PCD
对应一个 transforms_to_primary 条目；变换方向始终是“主坐标系 <- 源坐标系”。
插件必须原子地或一次性生成 output_pcd，失败时抛出异常。
"""

from pathlib import Path

import numpy as np

from ccs_monitor.map_building import write_binary_pcd
from ccs_monitor.map_fusion import transform_points
from ccs_monitor.point_cloud import MapPointCloudLoader


PLUGIN_API_VERSION = 1
ALGORITHM_ID = "example_concat"
DISPLAY_NAME = "示例直接拼接"
VERSION = "0.1.0"
DEFAULT_OPTIONS = {}


def fuse_maps(pcd_files, primary_frame, transforms_to_primary, output_pcd, options):
    """读取各 PCD、应用外参并输出一个 binary XYZ PCD。"""
    del primary_frame, options
    loader = MapPointCloudLoader()
    clouds = [
        transform_points(loader.load(path).points, transform)
        for path, transform in zip(pcd_files, transforms_to_primary)
    ]
    points = np.concatenate(clouds, axis=0).astype(np.float32)
    write_binary_pcd(Path(output_pcd), points)
    return {"point_count": len(points), "message": "示例拼接完成"}
