# Ground-Air AGV 地面任务部署记录

## 部署结果

- 设备：`AGV_001`，端侧 CCS 根目录 `/home/bitcq/ccs_edge_ws`。
- 部署标识：`agv_task_20260907_083858`。
- 上传包 SHA-256：`5ea964687f288c5eb5ce6055c365d81199f21f15ae172e2410432240577c3ee8`。
- 备份：`/home/bitcq/ccs_edge_ws/.deployment_backups/agv_task_20260907_083858`。
- 证据：`/home/bitcq/ccs_edge_ws/artifacts/agv_task_20260907_083858`。
- 端侧用户服务部署前为 `disabled/inactive`；静态验收结束时为 `disabled/active`，未启用开机自启动。

部署只替换以下 CCS 工作空间内容：

- `src/EPGeneral_task_control`
- `src/EPGeneral_ground_air_control`
- `config/ground_air_agv/task_control.yaml`
- `start_ccs_edge_dev.sh`

部署前后的 `/home/bitcq/catkin_ws/src` 共 563 个文件 SHA-256 清单一致。未修改车辆原生工作空间、用户 unit 或 `/etc`。

## 构建与增量测试

- 端侧隔离测试：任务协议包 30 项通过；Ground-Air 包 21 项通过。任务包中依赖平台源码的 `test_ground_contract` 只在本地运行，未向端侧复制平台包。
- 端侧增量构建：`catkin_make --pkg epgeneral_task_control epgeneral_ground_air_control -DPYTHON_EXECUTABLE=/usr/bin/python3` 通过。首次构建因设备时间仍为 1970 年产生 clock-skew 警告；NTP 同步后再次构建通过且无该警告。
- 部署后端侧测试：任务协议包 30 项、Ground-Air 包 21 项全部通过。
- 本地增量回归：任务协议包 31 项、Ground-Air 包 21 项、平台任务/AGV profile/设备地址/发行文档与版本 47 项，共 99 项通过。
- `roslaunch --nodes epgeneral_ground_air_control ground_air_task_control.launch` 只解析出 `/epgeneral_task_control` 与 `/epgeneral_ground_air_task_adapter`。

## 静态验收

指控平台已启动现有 NTP 和任务服务，UDP 123、14564 均监听。端侧随后报告 `NTPSynchronized=yes`，时间源为 `192.168.50.101`，满足任务协议 2 秒 UTC 容差。

一键脚本启动成功，常驻节点包含任务协调器、Ground-Air 任务适配器和地面控制层。无任务时不存在 `move_base` 或 `ground_air_mission` 节点，`/cmd_vel` 无发布者。车辆验收快照为未定位、`ALTCTL`；脚本没有自动切换模式、解锁或产生运动指令。

无运动 UDP 验收结果：

1. `negotiate_task` 被端侧接收并返回 `accepted=True`。
2. 两点、0.05 m/s 的地面任务通过分片、CRC 和 commit 校验，原子生成 568 字节 `trajectory.xml`，commit 返回 `accepted=True`。
3. 因 `/ground_air/localized=false`，任务状态进入 `failed`，没有启动导航或任务执行器；协调器按配置继续准备重试。
4. `delete_task` 返回 `accepted=True`，轨迹 XML 被删除，状态恢复 `no_task`；急停锁文件仍不存在。

静态验收期间设备发生一次整机重启。由于用户服务保持禁用，重启后没有自动恢复任务或运动；人工重新启动一键流程后，NTP、任务服务和上述静态验收再次正常。

最终交接核验发现此前验收 shell 仍持有一套交互式启动进程，而用户服务显示 inactive。已停止该交互栈并通过现有用户服务重新启动；随后用正常 `delete_task` 协议清理 `test_AG_` 验收任务。最终状态为服务 `disabled/active`、任务 `no_task`，仅任务协调器和 Ground-Air 适配器常驻，没有 `move_base`、任务执行器或 `/navigation/cmd_vel` 发布者，急停锁文件不存在。

真实急停没有在无人值守静态验收中触发，以免给已解锁车辆写入安全闭锁。已确认 `/ground_air/emergency_stop` 类型为 `ground_air_msgs/SetEmergencyStop`，无任务急停、机器人确认失败和重启闭锁由隔离测试覆盖。

## 回滚

停止本次手动启动的用户服务后，将备份目录中的两个包、AGV 任务配置和一键脚本按原路径恢复，再仅增量构建两个包。恢复后比较 `deployed-files.sha256`、重新执行包内增量测试，并保持 `ccs-edge-dev.service` 为 `disabled`。原生 `/home/bitcq/catkin_ws` 不参与回滚。

## 待完成验收

实车验收待完成。现场需先确认时间同步和重定位，由操作者人工解锁并切入 OFFBOARD，再用不超过 0.1 m/s 的短路线验证航点顺序、2 秒停留、进度反馈、普通停止和运动中急停。急停后的 POSCTL 切换和闭锁解除仍由操作者完成。
