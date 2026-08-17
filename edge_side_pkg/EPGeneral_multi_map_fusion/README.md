# epgeneral_multi_map_fusion

`epgeneral_multi_map_fusion` 是一次性离线融合工具：读取两张或多张 PCD 地图和设备摆放关系，将所有地图变换到指定参考地图坐标系，按体素质心融合并输出新的二进制 PCD 与 JSON 报告。输入地图始终只读。

首版只依赖 Python 3、NumPy 和 PyYAML，支持 ASCII/binary PCD。设备摆放或共享坐标系提供初始刚体变换；对有共同区域的边可启用内置 ICP 精配准。首版不做无初值全局特征搜索，也不隐式猜测设备位置。

## 构建

将 `edge_side_pkg` 放到 catkin 工作区的 `src` 后：

```bash
cd ~/catkin_ws
source /opt/ros/melodic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
```

## 任务 YAML

任务必须选择至少两张地图并给出连通的 `placements`。变换方向固定为：

```text
p_target = T_target_from_source * p_source
```

示例 `fusion_job.yaml`：

```yaml
schema_version: 1
reference_map_id: map_a

maps:
  - map_id: map_a
    pcd_path: /data/maps/a/map.pcd
  - map_id: map_b
    pcd_path: /data/maps/b/map.pcd
  - map_id: map_c
    pcd_path: /data/maps/c/map.pcd

placements:
  - source_map_id: map_b
    target_map_id: map_a
    kind: registration
    T_target_from_source: {x: 2.0, y: 0.0, z: 0.0, qx: 0.0, qy: 0.0, qz: 0.0, qw: 1.0}
  - source_map_id: map_c
    target_map_id: map_b
    kind: calibration
    T_target_from_source: {x: 3.0, y: 0.0, z: 0.0, qx: 0.0, qy: 0.0, qz: 0.0, qw: 1.0}

output:
  pcd_path: /data/maps/fused/map.pcd
  report_path: /data/maps/fused/fusion.json
```

`kind: calibration` 完全信任经测量/标定的变换；`kind: registration` 把所给变换作为 ICP 初值，并执行双向 fitness、对应数量和 RMSE 检查。未知摆放不能只靠 ICP，因为 ICP 是局部优化。

输出路径和报告路径必须尚不存在，且不得与任何输入 PCD 相同。相对路径按任务 YAML 所在目录解析。

## 运行

直接运行：

```bash
rosrun epgeneral_multi_map_fusion epgeneral_multi_map_fusion_node.py \
  --config-file "$(rospack find epgeneral_multi_map_fusion)/config/fusion.yaml" \
  --job-file /data/jobs/fusion_job.yaml
```

或使用 launch：

```bash
roslaunch epgeneral_multi_map_fusion epgeneral_multi_map_fusion.launch job_file:=/data/jobs/fusion_job.yaml
```

算法、质量门槛和资源上限位于 `config/fusion.yaml`。现场应先记录参考设备原点和朝向，再测量相邻设备的相对位姿；没有测量位姿时，各地图必须有足够且非退化的共同区域，并由上层提供可靠粗初值。
