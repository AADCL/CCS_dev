# Open3D 在线增量多尺度 ICP 融合插件

本目录提供一个符合 CCS_dev 融合插件 API v1 的增量地图融合示例。Python 文件的元数据、入口名称和五参数函数签名严格参照上级目录的 `map_fusion_plugin_example.py`。

## 文件

- `map_fusion_online_incremental_icp.py`：导入地面站的算法插件。
- `map_fusion_online_incremental_icp.json`：地面站算法配置对话框使用的 JSON 参数模板。
- `test_map_fusion_online_incremental_icp.py`：契约、配置、原子输出和 Open3D 数值测试。

## 运行依赖

插件需要 CCS_dev 已有 Python、NumPy、pypcd4 环境。多地图配准还要求：

```bash
python -m pip install "open3d>=0.18,<1"
```

Open3D 必须安装到启动地面站所使用的同一个 Python 解释器。单地图导入验证不加载 Open3D，因此即使开发机没有 Open3D，地面站仍能导入并检查插件格式；实际执行两张或更多地图时会明确提示缺少依赖。

## 导入和配置

1. 打开 CCS_dev 地图页面，进入“融合算法”。
2. 导入 `map_fusion_online_incremental_icp.py`。
3. 打开 `map_fusion_online_incremental_icp.json`，复制完整内容。
4. 将内容填入该算法的“JSON 参数”输入框并保存。
5. 新建或重新执行多机建图时选择“Open3D 在线增量多尺度 ICP”。

地面站现有导入器只复制 `.py` 文件，不会自动复制旁边的 JSON。JSON 文件是可编辑模板，运行时由地面站将其内容作为 `options` 参数传给插件；插件不会自行读取磁盘上的 JSON。

## 坐标与数据流

每个 PCD 必须有一个对应外参，方向固定为：

```text
主坐标系 <- 源坐标系
```

必须且只能有一张 `is_primary=true` 的主地图，且主地图使用单位外参。算法先应用人工外参，再以单位残差为初值进行粗、中、细三尺度 point-to-plane ICP。每张从地图通过质量门禁后立即加入内存中的累计目标地图，下一张从地图对更新后的累计地图配准。

这里的“在线增量”是单次 `fuse_maps()` 调用内逐张更新累计地图，不是点云切片实时回调，也不跨插件调用保存状态。现有建图过程中的实时预览仍使用 CCS_dev 内置体素累计器；停止建图后，本插件生成正式 PCD。

## JSON 参数

| 参数 | 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `voxel_sizes_m` | float 数组 | `[0.4, 0.2, 0.1]` | 粗到细的配准体素尺寸，必须严格递减 |
| `max_correspondence_distances_m` | float 数组 | `[0.6, 0.3, 0.15]` | 各尺度最大对应距离，必须严格递减 |
| `max_iterations` | int 数组 | `[60, 40, 30]` | 各尺度最大 ICP 迭代次数 |
| `relative_fitness` | float | `1e-6` | fitness 相对变化收敛阈值 |
| `relative_rmse` | float | `1e-6` | RMSE 相对变化收敛阈值 |
| `normal_radius_multiplier` | float | `2.5` | 法向搜索半径相对当前体素尺寸的倍数 |
| `normal_max_nn` | int | `40` | 法向估计最大邻居数 |
| `min_registration_points` | int | `30` | 每个尺度允许执行 ICP 的最少点数 |
| `min_fitness` | float | `0.35` | 最终尺度最低重叠 fitness |
| `max_inlier_rmse_m` | float | `0.15` | 最终尺度最大 inlier RMSE，单位 m |
| `max_residual_translation_m` | float | `0.5` | ICP 相对人工外参允许修正的最大平移，单位 m |
| `max_residual_rotation_deg` | float | `10.0` | ICP 相对人工外参允许修正的最大旋转角，单位 deg |
| `output_voxel_size_m` | float | `0.1` | 累计地图和最终输出的体素尺寸，单位 m |
| `max_output_points` | int | `5000000` | 最大输出点数，超限时失败而不是截断 |

三个多尺度数组长度必须一致，允许 1–5 个尺度。未知字段、布尔值冒充数字、非有限数、非正限制和不匹配数组会直接报错，避免参数拼写错误被静默忽略。

默认门限面向人工摆放、初始外参误差很低且来源之间有足够重叠的场景。若点云密度明显不同，应先调整体素尺寸和对应距离，再考虑调整质量门限。

## 输出与失败行为

插件返回：

- `point_count`：最终点数；
- `source_count`：输入地图数；
- `registered_source_count`：完成 ICP 的从地图数；
- `primary_frame`：主坐标系；
- `registrations`：按处理顺序保存每张从地图的 `fitness`、`inlier RMSE`、残差平移和残差旋转；
- `message`：中文执行摘要。

低 fitness、过高 inlier RMSE、残差超限、点数不足、无效参数或无效 PCD 会使整个融合失败。算法不跳过失败来源，也不会提交来源不完整的结果。最终 PCD 先写入同目录临时文件并重新读取校验，全部成功后使用原子替换；失败时清理临时文件并保留已有目标文件。

## 限制

- 只融合 XYZ，不处理颜色、强度、语义或 PGM。
- 不包含 RANSAC、FPFH、离群点滤波、全局配准或位姿图优化。
- 依赖低误差人工外参和足够的几何重叠。
- 增量结果受从地图输入顺序影响；插件固定遵循 `pcd_files` 原始顺序。
- 不解决大范围累计漂移或闭环。

## 测试

在 CCS_dev 根目录执行：

```bash
python -m compileall examples/0818
python -m json.tool examples/0818/map_fusion_online_incremental_icp.json
python -m unittest discover -s examples/0818 -p "test_*.py" -v
```

未安装 Open3D 时，契约、参数、单地图、原子输出和依赖错误测试仍会执行，只有合成点云数值配准测试显示 `skipped`。部署设备安装 Open3D 后，同一测试命令会自动执行全部数值用例。
