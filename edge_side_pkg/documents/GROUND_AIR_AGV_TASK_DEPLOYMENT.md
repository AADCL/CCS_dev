# Ground-Air AGV 地面任务部署

目标设备为 `AGV_001`。端侧部署根目录固定为 `/home/bitcq/ccs_edge_ws`；`/home/bitcq/catkin_ws`、系统 unit 和 `/etc` 只读。用户服务在部署前后必须保持 `disabled`。

部署清单包括 `EPGeneral_task_control`、`EPGeneral_ground_air_control`、`config/ground_air_agv/task_control.yaml` 和 `start_ccs_edge_dev.sh`。部署前将相同目标复制到 `.deployment_backups/agv_task_<UTC>/`，新文件先上传到 `.tmp/agv_task_<UTC>/`，核对 SHA-256 后再替换。失败时停止本次启动的服务，按清单从备份恢复，并仅增量重建两个受影响包。

增量构建：

```bash
cd /home/bitcq/ccs_edge_ws
source /opt/ros/noetic/setup.bash
source /home/bitcq/catkin_ws/devel/setup.bash --extend
catkin_make --pkg epgeneral_task_control epgeneral_ground_air_control \
  -DPYTHON_EXECUTABLE=/usr/bin/python3
```

无运动验收必须确认：

- `systemctl --user is-enabled ccs-edge-dev.service` 始终返回 `disabled`。
- 平台 UDP 14564 与 NTP 123 已监听，端侧 `NTPSynchronized=yes` 且使用 `192.168.50.101`。
- 一键启动后协调器、AGV 适配器和常驻控制节点存在，导航与任务执行器在没有已提交任务时不存在。
- 协商命令获得 ACK；未定位时提交任务只进入 `received/failed` 并自动重试，不产生非零速度、解锁或模式切换命令。
- 无已存任务的急停仍调用机器人服务；机器人未确认时 ACK 失败，确认后状态保持 `emergency_stop`。

现场实车验收另行进行：完成时间同步和重定位后，由操作者手动解锁并切入 OFFBOARD，使用 0.1 m/s 以内短路线验证航点、停留、终止和运动中急停。未完成该步骤时文档必须记录“实车验收待完成”。

2026-09-07 的实际部署、静态验收、证据目录和回滚基线见 `GROUND_AIR_AGV_TASK_DEPLOYMENT_LOG.md`。
