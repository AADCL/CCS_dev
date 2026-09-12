# epgeneral_task_control

配套 CCS 0.23.1：[完整使用手册](../documents/USER_MANUAL.md) · [设备内接口与参数](../documents/INTERFACE_REFERENCE.md)。包级 launch 默认读取共享配置包；一键脚本显式读取工作空间 `config/<profile>`，修改后需重启。

<!-- epgeneral_task_control_VERSION: 0.5.1 -->

版本：`v0.5.1`。运行配置统一由 `epgeneral_device_config/config/task_control.yaml` 提供。Scout Mini 继续通过 `/move_base` 执行任务；Ground-Air AGV 通过原生任务服务执行仅地面航点，并要求实时定位、人工解锁和 OFFBOARD。

## Go2 显式控制服务接入

`navigation_management` 默认 `managed`，由适配器启动和管理导航进程；Go2 使用 `attach` 复用重定位会话启动的导航，不停止其原生进程。`auto_arm_on_schedule`、`auto_disarm_on_terminal` 默认均为 `false`，不改变 Scout 或其他设备的启动和控制方式。

Go2 专项配置同时启用两项自动控制策略，并提供 `localization_ok_topic`、`control_enabled_topic`、`navigation_reset_service`、`control_enable_service`、`control_service_timeout_seconds`、`control_state_timeout_seconds`、`emergency_stop_state_file`。自动使能必须同时启用终止停用。

原生 Go2 `/go2/control/enabled` 仅在变化时锁存发布，需配置 `control_diagnostics_topic: /go2/diagnostics`，使用每秒发布的 `GO2 SDK bridge` 状态中 `motion_enabled` 字段刷新一致的控制状态。诊断必须有新鲜时间戳，不得覆盖与 Bool 冲突的值；服务确认还要求消息序号增加且生成于服务调用之后。可选 `control_diagnostics_status`、`control_diagnostics_key` 用于同类显式控制接口，默认不为其他设备订阅诊断。

只有计划开始时刻到达且定位、位姿和已停用状态新鲜时，才调用 `std_srvs/Trigger` reset 并检查 `success`，随后调用 `std_srvs/SetBool(true)`，确认新鲜 enabled 状态后发送目标。完成、停止、卸载、失败和关闭均取消目标、发送零速、检查停用服务响应，并等待服务调用后收到的新鲜 disabled 状态；停用失败反馈失败并持久闭锁。运行中定位或控制状态失效同样终止执行。

`EMERGENCY_STOP` 持久化闭锁后执行取消、零速及确认停用，成功才反馈 `emergency_stopped`。重启、准备和新任务不能解除闭锁。人工调用 `/epgeneral_navigation_task_adapter/reset_emergency_stop`（`std_srvs/Trigger`）时必须没有活动执行、准备线程或控制调用，且底盘状态新鲜并为 disabled；解除只移除闭锁，不使能底盘。该服务仅在配置了显式控制策略时创建。

ROS 服务调用超时后会持久闭锁；尚未返回的 RPC 继续占用控制串行锁，禁止人工解锁。若使能 RPC 延迟返回，后台在释放锁前尝试补偿停用。无法确认停用时不得将失败解释为机器人已停止，需要现场检查底盘状态。

Go2 控制转换期间按原执行的 `scheduled` 或 `running` 状态保活，不提前报告完成或停用。保活和终态不等待 TF 查询，避免合法慢服务先触发协调器反馈超时；异步 watchdog 和执行线程携带执行身份，旧执行的迟到终态不能停用或清空新执行。

任务准备阶段会使用导航 `map.yaml` 和 PGM 检查全部航点。地图外、障碍区或未知区航点返回 `WAYPOINT_NOT_TRAVERSABLE`，不会进入 `ready`。运行期 `move_base` 规划失败返回 `NAVIGATION_PLAN_FAILED` 并保留 action 状态文本。

节点监听 UDP 14563，向地面站 UDP 14564 发送 ACK、1 Hz 心跳、任务状态和航点进度。任务文件完整提交后，Scout 适配器启动并保持 `scout_navigation navigation_teb.launch map_name:=<map_id>`；执行和常规停止只发送或取消目标，删除、急停和节点关闭才停止导航进程。

Ground-Air AGV 使用 `ground_air_task_control.launch`。协调器常驻接收任务，适配器在 PREPARE 后按需启动原生 `car_bringup/task_system.launch` 的导航和任务层；控制层由一键脚本常驻，以便没有已存任务时仍可确认 `/ground_air/emergency_stop`。机器人未确认急停闭锁时，协议不会返回成功 ACK。

## 状态与数据

- 接收状态：`no_task -> receiving -> received（导航准备中） -> ready`，错误进入 `failed`；可恢复准备错误按周期重试，`EMERGENCY_STOP_LATCHED` 停止自动重试，急停依次进入 `emergency_stop -> no_task`。
- 执行状态：`scheduling -> scheduled -> running -> completed/stopped/failed`。
- 轨迹网络内容保持 zlib JSON；v2 清单和子任务 JSON 写入 `~/ccs_edge_ws/mission/<task_id>/`，执行兼容 XML 写入同一 mission 根目录下的 `<ID哈希>/trajectory.xml`。
- XML 保存任务、子任务、设备、修订、CRC、地图/frame、速度、延迟和有序 XYZ 航点。一次原子替换失败不会破坏旧修订。
- 同一设备只允许一个 execution；进程重启不会恢复运动，会向 ROS 适配器发布 CANCEL 或 STOP。
- Scout 默认要求每次准备在 25 秒内连接 `/move_base`，单航点超时 300 秒；准备期间每秒反馈状态，可恢复失败后按配置周期重试，持久急停拒绝必须人工复位后重新下发。
- 执行前必须同时存在有效的实时 `/fastlio_odom` 和 `map<-odom` TF；仅有历史 `relocalization.json` 不允许启动导航。TF 查询或位姿转换异常统一反馈 `LOCALIZATION_UNAVAILABLE`，不会从 ROS callback 泄漏异常。

ROS command 包含 `PREPARE/SCHEDULE/CANCEL/STOP/UNLOAD`、request/task/subtask/device/execution ID、revision、XML 路径、map/frame 和 UTC 启动时间。feedback 必须回传相同 ID/revision/request ID；准备阶段返回 preparing/ready/failed，执行阶段返回 scheduled/running/终态、航点和位置。

`ros.status_topic` 额外发布 latched `std_msgs/String`，内容为当前接收或执行状态。Go2 profile 将其设置为 `/qrd/QRD_001/task_status`，供 MQTT 健康状态订阅。

## GO2 恢复与退出

协调器复用 adapter.emergency_stop_state_file，在协商、prepare、commit 检查锁存，传输中新出现或损坏标记也拒绝；适配器保留最终检查。下发/commit 成功不等于导航 ready，更不等于正在执行。

修复根因后，由操作人员在无准备/执行/控制过渡且持续新鲜 disabled 的条件下调用 /epgeneral_navigation_task_adapter/reset_emergency_stop（std_srvs/Trigger），确认 success 后重新下发。重启、新任务和回滚不得删除或覆盖最新安全标记，复位服务不使能。操作命令见[使用手册](../documents/USER_MANUAL.md#go2-人工急停复位)。

根脚本先停止任务再停用底盘，master 最后退出。适配器将内部 UNLOAD/request_id=shutdown-unload 与 ROS shutdown（含 is_shutdown_requested 阶段）统一为幂等关闭；内部卸载消息可以先于本进程的关闭信号到达。关闭先阻止新 PREPARE/SCHEDULE/复位并停止监控，再串行取消目标、发布零速、确认停用和清理自有导航，重复关闭复用首次结果。仅关闭上下文允许复用新鲜 disabled，且必须同时排除未完成的控制过渡及 RPC；普通 STOP/UNLOAD 和非 ROS 关闭仍需服务应答，真实失败继续锁存。见 [QRD_002](../deploy/records/QRD_002/DEPLOYMENT.md)、[QRD_003](../deploy/records/QRD_003/DEPLOYMENT.md) 的部署记录。

当前源码进一步区分协调器卸载后的可恢复状态和永久 close/ROS 退出：前者完成收尾后，新 PREPARE 重新校验急停、地图、定位并恢复监控；后者继续拒绝工作。该后续修订仅本地验证、尚未部署，不属于 2026-09-12 端侧清单哈希。避免把关闭幂等实现成永久 BUSY。联合下发还需在 transfer_seconds 内连续传输并取得本轮 XML committed 证据，不能仅用旧任务幂等 ACK 证明落盘。完整经验及现场确认来源见 [GO2 部署经验](../documents/GO2_DEPLOYMENT_LESSONS.md)。

## 安装与启动

```bash
sudo apt update
sudo apt install python3-yaml python3-msgpack python3-catkin-pkg python3-rospkg \
  ros-melodic-geometry-msgs
cd ~/c3po_ctrl_ws
source /opt/ros/melodic/setup.bash
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
roslaunch epgeneral_task_control epgeneral_task_control.launch
```

启动前修改 `epgeneral_device_config/config/task_control.yaml` 的地面站 IP，并保证 `epgeneral_device_config/config/device.yaml` 与地面站设备 ID/IP 一致。设备和地面站都应使用 NTP；放行端侧 UDP 14563 和地面站 UDP 14564。

```bash
rostopic info /epgeneral_task_control/execution_command
rostopic echo /epgeneral_task_control/execution_feedback
sudo tcpdump -ni any 'udp port 14563 or udp port 14564'
PYTHONPATH=src python3 -m unittest discover -s test -v
python3 scripts/check_version.py
```

协议运行于可信局域网，不提供认证、加密、可靠流传输或拥塞控制。轨迹坐标为地图 frame 下的局部 ENU 米制 XYZ；跨地图 TF、经纬度、避障和动力学控制由设备专属执行节点负责。
