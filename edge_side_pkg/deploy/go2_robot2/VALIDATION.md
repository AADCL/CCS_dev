# Go2 Robot 2 部署与增量验证记录

## 验证范围与版本

本记录对应 2026-09-09 的 `QRD_002`（`unitree@192.168.50.111`）部署。
工作空间为 `/home/unitree/ccs_edge_ws`，原生 underlay 为
`/home/unitree/go2_nav_ws`。代码基线为 `origin/main`：
`d597f0ed727bc7048268faf659432f93af3e0609`。

本轮部署版本：任务包 `0.5.1`、重定位包 `0.4.0`、Go2 集成包 `0.1.2`。
旧 `go2_edu` 和其他设备配置未更改；原生导航工作空间未修改、未重新编译。
所有 PID 和计数均为本次观测证据，不应作为后续停机时可直接复用的进程标识。

## 已完成结果

| 检查项 | 实际结果 |
| --- | --- |
| 源码布局 | 8 个 CCS 功能包实体已移到 `src`；源码目录无软链接 |
| 旧目录清理 | `--finalize` 已删除活动 `vendor`、`bin`，保留可恢复备份 |
| 构建 | 唯一一次 `catkin_make -j2 -l2 -DCMAKE_BUILD_TYPE=Release` 成功；`build.ok` 时间为 2026-09-09 10:46:40 +08:00 |
| 预检查 | 根脚本 `--check` 通过，SNTP 偏差约 -0.002 秒；未步进系统时钟 |
| 常驻启动 | 新根脚本观测 PID 为 61296，9 项直接 launch 均就绪；底盘使能状态为 `false` |
| 启动授时检查 | 启动日志 SNTP 偏差约 -0.003 秒，正常启动只查询、不执行 `--set` |
| 服务握手 | 在独立 ROS master `11321` 上，以真实 ROS `Trigger/SetBool` 服务类型和 mock 处理器验证通过；reset 拒绝时不使能 |
| 验证隔离 | 上述握手测试对生产 reset/enable 服务的调用次数为 0 |
| 指控平台恢复 | 正常关闭旧 PID 4624 后，在原工作目录使用 `run.py` 重启一次；新观测 PID 为 14152，stderr 为空 |
| 平台端口 | 重启后 `14560/14562/14564/14566` 和 NTP `123` 均监听 |

### UDP 真机接收

正常关闭旧指控进程后，在正式 `14560` 端口使用正式解析器和数据存储逻辑
接收 Go2 真实数据 8 秒，结果如下。该检查不是只抓取网络包，而是验证正式
协议校验后的入库结果；完成后释放端口，再启动指控平台。

| 数据类别 | 接收数 |
| --- | ---: |
| 一级遥测 | 161 |
| 二级遥测 | 40 |
| 三级遥测 | 8 |
| 心跳 | 8 |

解析告警为 `{}`，IMU 可用，点云状态约 10 Hz。8 项描述集合保持严格哈希校验：
`832977e1229667bc8a49936e707473702756c8203aeb3e77fbfb727131590f02`。
遥测 IMU 实际取自 `/go2/imu`；`MID360 IMU` 仅保留为兼容描述名。
FAST-LIO 的输入仍为 `/livox/imu`，未更改其他设备 IMU 来源或放宽四元数校验。

上述零告警仅对应 8 秒探测窗口，不代表正式平台持续零告警。截至
2026-09-09 11:03:31 +08:00，正式平台日志累计出现 21 次 `out_of_order`，
首次为 10:59:56，相关遥测序号范围为 13175 至 17477；未出现新的描述集合
哈希拒收。平台会严格丢弃这些乱序包，本轮未放宽保护，也未为此再次重启。
后续只读观察截至 11:05:58 累计为 60 次。发送端各级独立计数且串行发送，
接收端按设备、会话、消息类型和级别隔离序号，未发现明显计数冲突。
现有日志不足以区分网络重排或延迟重复，不能据此将其归因于 WiFi，
也不宣称长期零丢包；此项保留为网络侧后续观察风险。

### 控制状态与安全边界

`/go2/control/enabled` 是状态变化时更新的锁存 Bool，不能单凭最近收到该
锁存值就认为底盘状态新鲜。适配器同时使用周期 `/go2/diagnostics` 中
`GO2 SDK bridge / motion_enabled`，要求时间戳有效且新鲜、与 Bool 一致，
并在服务调用后等待新的状态回调序号，避免用旧 Bool 或旧诊断确认使能/停用。

reset 使用 `std_srvs/Trigger`，且检查 `success` 后才允许进入使能流程。
mock 测试覆盖 reset 拒绝、超时、陈旧状态、定位丢失、取消与使能竞态、停用
失败、急停确认与人工恢复。急停状态文件配置为
`run/state/go2_task_safety.json`，不能通过新任务或重启自动清除；
`/epgeneral_navigation_task_adapter/reset_emergency_stop` 仅在无活动任务且
确认底盘已停用时允许人工清除，不会自动使能。

本轮没有执行真实使能、运动目标、新重定位或完整建图，也没有进行真实运动
验收。真实轨迹执行仍需操作员在现场完成定位、确认安全场地后另行验收。

## 旧状态说明

重启后，旧任务恢复时的 `PREPARE` 被 `LOCALIZATION_UNAVAILABLE` 拒绝，
因此保留为失败状态。这是新常驻链尚未完成新定位时的保护结果，不是本轮
`Trigger` 握手失败，也不能据此认为 UDP 修复无效。本轮未清理用户任务数据。

`ccs-sntp-sync.service` 的 `ExecStart` 已迁移到
`/home/unitree/ccs_edge_ws/scripts/ccs_sntp_sync.py`，并重新加载单元定义。
观察到的 `ActiveState=failed` 是既有历史状态：2026-09-08 22:29:06 启动，
22:31:28 报 `SNTP query failed: timed out`。本轮没有重启该设时服务，也没有
清除失败状态；正常启动与预检查的 SNTP 查询已通过，但这不等于完成了开机
授时服务实测，不应将历史失败误报成本轮新故障或宣称该服务已验证恢复。

## 增量测试

按受影响范围分组验证，源码或配置修正后只重跑对应项，没有重复完整建图或
原生算法构建。

| 分组 | 通过数 |
| --- | ---: |
| UDP 契约、发布归档、源码布局 | 38 |
| 任务控制与 Go2 安全逻辑 | 68 |
| Go2 profile | 10 |
| 互斥守卫 | 5 |
| 建图与重定位 | 73 |
| 版本检查 | 5 |

根启动和迁移脚本的 `bash -n` 检查通过，ROS 验证辅助程序的 Python 编译
检查通过。发布测试确认新集成包与专用部署资料进入发布归档。现场使用一个
独立 mock master、一次 CCS 构建、一次常驻链切换和一次指控平台重启完成验证。

## 备份与载荷

备份目录：
`/home/unitree/ccs_edge_ws/backups/go2-physical-20260909T024613Z`。

原始载荷暂存目录：
`/home/unitree/ccs_edge_ws/run/deploy-go2-20260909-physical-v1`。
载荷共 172 个文件，暂存目录及原始校验清单保持不变。

| 对象 | SHA-256 |
| --- | --- |
| `before.tar.gz` | `8094ef430b0c3055b43ffb3cf9b11a9f46a998d39ad80de2690e095037609cda` |
| 备份的 `ccs-sntp-sync.service` | `815df7319de1579bcee6207e3358f602edc5138e417bb5311a255e8ca590bb9f` |
| 原始 `payload.sha256` | `31214459ea82983d311530c363b2796614b27f59505bd390a1a46d9909ca05dd` |
| 原始载荷 tar | `592997bb0f1bbb7a37b457c21986c52b71c755fd5e0b0cdcbf588280bf47cc42` |

备份覆盖旧源码、软链接及其 vendor 实体、配置、启动脚本、构建结果和授时
单元。地图、任务、日志与运行安全状态保留。删除的 `vendor/bin` 可以通过
该备份恢复；不能混用旧生成消息和新任务节点代码。

`--finalize` 已在原始载荷完整校验成功后执行。之后仅追加或更新
`docs/go2_robot2` 下的 README、部署说明与本验收记录，以及集成包 README
中的验收文档链接，不修改功能代码或原始载荷清单。
这些后补文档不属于上述 172 文件快照；不要重算历史校验值，也不要为了
追加验收文字重复执行已完成的一次性迁移或 finalize。

## 迁移与回滚用法

迁移实现见 [migrate_workspace.sh](migrate_workspace.sh)，日常启动和原生参数
对应关系见 [DEPLOYMENT.md](DEPLOYMENT.md)。以下 `--check/--apply` 仅适用于
尚未迁移的旧 vendor 布局；当前设备已完成，不需要再执行，否则会因不再
存在预期旧软链接而拒绝操作。

```bash
MIGRATION_STAGE=/home/unitree/ccs_edge_ws/run/deploy-go2-20260909-physical-v1
MIGRATION_SCRIPT="$MIGRATION_STAGE/docs/go2_robot2/migrate_workspace.sh"
ROLLBACK_DIR=/home/unitree/ccs_edge_ws/backups/go2-physical-20260909T024613Z

# 旧布局迁移前：只校验载荷、设备身份和软链接目标。
bash "$MIGRATION_SCRIPT" --check "$MIGRATION_STAGE"

# 在任务空闲、已正常停止自有启动流程后，于同一个 Bash 中执行。
sudo -v
source "$MIGRATION_SCRIPT" --apply "$MIGRATION_STAGE"
```

使用交互 SSH 时可分配 TTY，由 `sudo -v` 安全提示输入。非 TTY 自动化必须
在同一个 Bash 中完成受保护输入的 `sudo -S -v` 和
`source "$MIGRATION_SCRIPT" --apply "$MIGRATION_STAGE"`，不要另开子 Bash；
否则 sudo 的父进程隔离可能使脚本内 `sudo -n` 无法复用认证。不得将密码
写入源码、文档命令、日志、环境配置或 PR。本轮首次子 Bash 尝试即在备份、
文件变更和构建之前因该认证问题退出；改为同一 Bash 后成功，只构建了一次。

首次迁移后，运行根脚本 `--check`，完成对应增量验收，然后才最终清理：

```bash
source "$MIGRATION_SCRIPT" --finalize "$ROLLBACK_DIR"
```

需要回滚时，先确认任务空闲并正常停止本次自有根启动流程；优先在其终端
按 Ctrl+C。若使用进程信号，必须先重新核实 PID 和命令归属，不复用本记录
中的旧 PID，也不使用 `killall/pkill` 清理未知进程。迁移脚本自身不会停止
服务；如果仍有 ROS 流程或活动任务，会拒绝继续。

确认自有流程已退出后，在同一 Bash 中执行：

```bash
MIGRATION_STAGE=/home/unitree/ccs_edge_ws/run/deploy-go2-20260909-physical-v1
MIGRATION_SCRIPT="$MIGRATION_STAGE/docs/go2_robot2/migrate_workspace.sh"
ROLLBACK_DIR=/home/unitree/ccs_edge_ws/backups/go2-physical-20260909T024613Z
sudo -v
source "$MIGRATION_SCRIPT" --rollback "$ROLLBACK_DIR"
```

回滚会先验证备份校验值和 sudo 权限，将当前部署保存在备份目录的
`failed-<UTC时间>` 下，再恢复旧部署和授时单元定义。地图、任务、日志与
`run` 状态仍保留。恢复后检查旧入口和状态再启动，不能默认启用真实运动。
