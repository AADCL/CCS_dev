# epgeneral_ground_air_control

当前独立版本 **0.1.0**，配套 CCS 0.23.1。仅 Ground-Air 设备安装此包；它依赖车辆 underlay 的 ground_air_msgs 及算法栈，不属于其他设备的公共七包。

[使用手册](../documents/USER_MANUAL.md) · [完整接口与参数](../documents/INTERFACE_REFERENCE.md) · [Ground-Air 部署](../documents/GROUND_AIR_AGV_DEPLOYMENT.md)

## 职责

- ground_air_stage_manager_node.py 提供 /ground_air/system/set_stage，管理基础(0)、建图(1)、重定位(2)的互斥阶段。
- ground_air_relocalization_stage_node.py 以会话归属协调重定位阶段，关闭时只释放自身阶段。
- ground_air_initial_pose_adapter_node.py 将 /initialpose 转为地图加载和 use_initial_guess=true 的重定位调用。
- 当前 manager 发布 guard 2 和 external_tf_required=1；静态 odom/camera_init、base_link/body 变换由一键脚本持有，不由 manager 重复启动。

## 安装与运行

先 source Noetic、/home/bitcq/catkin_ws/devel/setup.bash，再在 CCS 工作空间构建本包和七个公共包，确认 rospack find ground_air_msgs 成功。日常启动按设备指南手动 start 用户 ccs-edge-dev.service，保持开机自启动 disabled。

单节点排障应先停止一键服务，正确 source 后执行：

~~~bash
rosrun epgeneral_ground_air_control ground_air_stage_manager_node.py
~~~

这不会代替外部静态 TF、飞控、雷达及算法。完整定位通过 epgeneral_relocalization 自动启动局部 car_bringup override；手动测试 relocalization_control.launch 时必须提供 map_id，maps_root 默认 /home/bitcq/ccs_edge_ws/maps/download，两个超时默认 90/60 秒。

## 验证与停止

~~~bash
rosparam get /ground_air_stage_manager/ccs_session_guard_version
rosparam get /ground_air_stage_manager/external_tf_required
rostopic echo -n 1 /ground_air/system/stage
rosservice type /ground_air/system/set_stage
rossrv show ground_air_msgs/SetSystemStage
~~~

预期 guard=2、external_tf_required=1。外部 .srv 定义不随本仓库维护，应在设备核实。独立节点按 Ctrl+C 停止；整机使用 systemctl --user stop ccs-edge-dev.service。不要通过手动 set_stage 抢占其他 caller/map_id 的会话。

旧 deploy_stage_manager_update.sh 和 car_bringup_scripts 是 underlay 历史实现；当前部署使用本包。阶段拒绝时检查 caller/map_id、服务类型、外部 TF 和已有阶段，回滚使用同一批次配置及源码。
