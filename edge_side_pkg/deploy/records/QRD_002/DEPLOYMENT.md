# QRD_002 部署与验收记录

合并日期：2026-09-11；操作基线：CCS 0.23.1。此文件是该设备唯一的部署记录入口，后续按日期追加。

## 当前入口

- 设备：QRD_002；profile：`go2_robot2`；端侧：`unitree@192.168.50.111`。
- [配置与脚本](../../go2_robot2/)保持原位置；[从零部署](../../../documents/DEPLOYMENT_GUIDE.md)、[接口填写](../../../documents/CONFIG_TOPIC_REFERENCE.md)、[使用手册](../../../documents/USER_MANUAL.md)、[设备索引](../../README.md)。
- 原生 Go2，重定位管理导航，任务 attach。2026-09-12 已部署退出急停修复、服务复位及受控重启，联合下发通过，最终 CCS 运行且 disabled；定位 standby，未做运动验收。见[最新记录](#joint-shutdown-20260912)。Robot2 仍未配置独立 MQTT connection，授时/日志沿用本设备策略，不能视为同步了 Robot3 全部行为。旧部署请求仅为历史需求，不是新的授权或验收事实。

- 2026-09-12 后续：用户已确认本轮落地测试完成，见[现场确认](#field-confirmation-20260912)。此前 disabled/standby 是工具验收时快照，不作为当前实时状态；后续未部署修订的状态保持。

## 历史材料与来源校验

以下合并原文件，保留日期、版本、哈希、失败证据、跳过项和原操作示例。历史章节的“当前/最终运行”、旧日志、相机筛选、时差门控、旧服务/保存路径及退出方式仅适用于当时；新部署执行上方当前指南。未记载实测不能补写通过，旧请求不构成新任务指令。

| 原文件（相对 edge_side_pkg） | 原始字节 SHA-256 | 合并章节 |
| --- | --- | --- |
| `deploy/go2_robot2/README.md` | `c92cf7c18ffa7dc70068f2908e53c9fe9cb5c45664f3fba8454e2dda905bb0c9` | [材料 1](#source-1) |
| `deploy/go2_robot2/DEPLOYMENT.md` | `905404f16f038983f8028b35a868f0f9752ba2cf78f600e8b6a2c4514406f626` | [材料 2](#source-2) |
| `deploy/go2_robot2/VALIDATION.md` | `79e5846569096599ab810c9548caf1e598202f08559d73626e052ea7fa6fcba7` | [材料 3](#source-3) |
| `deploy/EDGE_DEVICE_DEPLOYMENT_REQUEST.md` | `a220214acc20c74f0bf6815219934b600bd6122d0035b698cdc109ce1df46ca5` | [材料 4](#source-4) |

<a id="source-1"></a>

## 历史材料 1：deploy/go2_robot2/README.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s1-go2-robot-2-端侧部署"></a>

### Go2 Robot 2 端侧部署

本目录只对应 `QRD_002 / 192.168.50.111`，原生工作空间为
`/home/unitree/go2_nav_ws`，CCS 工作空间为 `/home/unitree/ccs_edge_ws`。
其他设备继续使用各自 profile，不部署本目录覆盖旧 `go2_edu`。

- [部署结构、日常启动与原生 launch 参数](../../go2_robot2/DEPLOYMENT.md)
- [本次真实部署结果、增量测试、已知状态与备份校验值](../../go2_robot2/VALIDATION.md)
- [一次性迁移、最终清理与回滚用法](../../go2_robot2/VALIDATION.md#迁移与回滚用法)
- [迁移脚本](../../go2_robot2/migrate_workspace.sh)

当前物理目录迁移与 `vendor/bin` 清理已经完成；日常只需使用工作空间根
`start_ccs_edge_dev.sh`，不要重复执行一次性迁移。真实运动尚未验收。

<a id="source-2"></a>

## 历史材料 2：deploy/go2_robot2/DEPLOYMENT.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s2-go2-robot-2-deployment"></a>

### Go2 Robot 2 Deployment

This profile is exclusively for `QRD_002`, `unitree@192.168.50.111`. Keep
`go2_edu` and all other device profiles unchanged. Do not deploy the control
platform or a full CCS repository on the robot.

See [the deployment validation record](../../go2_robot2/VALIDATION.md) for measured results,
backup checksums, known historical state and validation limits. The
[migration and rollback procedure](../../go2_robot2/VALIDATION.md#迁移与回滚用法) gives the exact
one-time command workflow for [migrate_workspace.sh](../../go2_robot2/migrate_workspace.sh).

<a id="s2-source-of-truth-and-layout"></a>

#### Source of Truth and Layout

The native underlay is `/home/unitree/go2_nav_ws`; the CCS overlay is
`/home/unitree/ccs_edge_ws`. The native `README.md`, `STARTUP_GUIDE.md`, and
the launches in `src/go2_bringup`, `go2_core`, `go2_control`,
`go2_mapping`, `go2_localization` and `go2_navigation` were inspected.
Some native prose still names the older nvidia/.100 installation. For this
device, the actual unitree paths and launch parameters take precedence.

The overlay contains eight physical source-package directories: device config,
GO2 integration, map stream, MQTAV, relocalization, task control, UDP telemetry
and video SRT. They reside directly under `src`, not behind package symlinks.
The native underlay remains separate; it is not copied into the overlay.

| Workspace location | Contents |
| --- | --- |
| `src/<package>` | Real CCS source files, generated ROS messages built together |
| `config/go2_robot2` | The YAML files from this deployment profile |
| `start_ccs_edge_dev.sh` | The script in this directory, installed executable |
| `scripts/ccs_sntp_sync.py` | SNTP helper, installed executable |
| `run/managed`, `logs/managed` | Owned process IDs and per-launch logs |
| `run/state/go2_task_safety.json` | Persistent task emergency-stop latch |
| `maps`, `mission`, `logs` | Existing device data; preserve during migration |

Neither `vendor` nor `bin` is part of the final runtime layout. Keep migration
backups outside those directories. A rollback must restore a matched set of
source, generated messages, profile and startup files.

<a id="s2-startup-and-native-parameters"></a>

#### Startup and Native Parameters

Run from the robot:

```bash
cd /home/unitree/ccs_edge_ws
./start_ccs_edge_dev.sh --check
./start_ccs_edge_dev.sh
```

`--check` (alias `--preflight`) verifies configuration, dependencies, addresses,
clock offset and persistent launch parsing. It does not start/stop nodes or set
the clock. Normal startup is foreground and uses the same managed-process model
as Scout/Wheeltec, but refuses existing conflicting GO2/CCS nodes instead of
taking over an unknown workflow. An existing ROS master can be reused.

The script explicitly loads ROS Noetic, the native underlay and the CCS overlay
in that order. It calls the following functional launches directly:

| Lifecycle | Launch and key parameters |
| --- | --- |
| Persistent LiDAR | `livox_ros_driver2 msg_MID360.launch` |
| Persistent chassis | `go2_control control.launch use_real_sdk:=true network_interface:=go2dds`; bridge starts disabled |
| Persistent RGB | `realsense2_camera rs_camera.launch`; D435i, 640x480 at 30 FPS, depth/IR/IMU/TF disabled |
| Persistent CCS | MQTAV, UDP, video SRT, map stream, relocalization and navigation task launches, each with explicit device/profile YAML |
| Mapping FAST-LIO | Integration `mapping_fast_lio.launch`, shared exclusive lock |
| Mapping prerequisites | `mapping_prerequisites_go2_robot2.launch`: native core with explicit extrinsics, no costmap cloud, accumulator output path passed by map stream |
| Map export | `go2_mapping export_occupancy.launch`: explicit input PCD, output PGM and YAML paths |
| Relocalization | Exclusive guard, then integration `navigation.launch` with `map_name:={map_id}`, `map_root:={map_root}` and explicit native extrinsics |
| Task execution | Attach to the already-localized navigation stack; no duplicate native total launch |

Do not invoke native total `mapping.launch` or `navigation.launch` from the
root script: those include persistent drivers/control and would duplicate their
owners. FAST-LIO still consumes Livox IMU, not the chassis IMU.

Defaults are LAN `192.168.50.111/24`, DDS `go2dds=192.168.123.18/24`,
ground station `192.168.50.101`, and native extrinsics
`/home/unitree/go2_nav_ws/src/go2_core/config/extrinsics.yaml`.
Environment overrides include `CCS_EDGE_WORKSPACE`, `CCS_GO2_NAV_WORKSPACE`,
`CCS_EDGE_PROFILE_CONFIG_DIR`, `CCS_GROUND_STATION_IP`, `CCS_ROS_IP`,
`CCS_GO2_NETWORK_INTERFACE` and `CCS_GO2_USE_REAL_SDK`. Path/network overrides
require matching profile YAML; they do not silently rewrite profile storage.
`CCS_GO2_USE_REAL_SDK=false` selects the native mock bridge for isolated
validation, while sensor launches remain real.

On exit, task command consumption is stopped first, the owned real bridge is
disabled, and owned launches are stopped in reverse order. Only a ROS master
started by this script is stopped. Unknown node owners are never killed.

<a id="s2-failure-analysis-and-compatibility"></a>

#### Failure Analysis and Compatibility

UDP packets arrived at the platform, but the exact eight-entry GO2 descriptor
set was absent from its accepted contracts. Add that set to development and
release UDP configurations while retaining strict hash validation and all
existing descriptor sets. Heartbeats carry the same contract and were rejected
for the same reason.

The accepted eight-descriptor SHA-256 is
`832977e1229667bc8a49936e707473702756c8203aeb3e77fbfb727131590f02`.

This profile sends IMU telemetry from `/go2/imu`, whose quaternion is valid.
`/livox/imu` remains an algorithm input but does not provide valid orientation
for the platform IMU contract. The descriptor display name `MID360 IMU` is
retained intentionally as a wire-contract compatibility label; changing it
would change the descriptor hash. Other devices' sources and validators remain
unchanged.

The native reset service is `std_srvs/Trigger` (MD5
`937c9679a518e3a18d831e57125ea522`), not `std_srvs/Empty` (MD5
`d41d8cd98f00b204e9800998ecf8427e`). The adapter must check the Trigger success
response before enabling the SDK bridge through `std_srvs/SetBool`.

The mapping preview's `lio_odom` and accumulator artifact's `odom` are distinct
intentional frames. Keep `artifacts.frame: odom`, the native pose/cloud adapters,
and the control-side Go2 artifact-frame contract aligned. Do not globally rename
frames or fabricate a transform for other devices.

The task profile attaches to the selected map, gates on fresh localization,
arms at execution time, and confirms disable on terminal states. Emergency stop
uses the periodic `/go2/diagnostics` bridge `motion_enabled` state for freshness
alongside `/go2/control/enabled`, which is latched and updates only on changes.
The safety latch
persists in `run/state/go2_task_safety.json`; a new task or process restart must
not clear it. The device-only `std_srvs/Trigger` service
`/epgeneral_navigation_task_adapter/reset_emergency_stop` is the explicit
operator reset, allowed only without an active execution and after fresh
confirmation that the bridge is disabled. Reset never enables motion.

<a id="s2-migration-and-clock-service"></a>

#### Migration and Clock Service

Use the reviewed migration script for the verified .111 paths only. Inventory
each package link and resolved target before moving anything. Archive the old
source/config/startup/service state and build outputs, retain a checksum, move
the real package directories to `src`, overlay the reviewed CCS changes, then
rebuild the CCS workspace once against the unchanged native underlay. This
one-time path change invalidates old CMake/generated-message references.

Install this profile's `ccs-sntp-sync.service` under
`/etc/systemd/system` and reload systemd after the helper has been placed in
`scripts`. Its explicit `--set` operation belongs to the boot-time service,
not the application startup path. Do not step the system clock during a task.
The normal script only queries the ground-station SNTP server and requires
absolute offset no greater than five seconds.

Remove `vendor` and `bin` only after verifying all source contents, regenerated
package paths and live service/startup references. Preserve user maps, missions,
logs and native workspace. On failure, stop only this deployment, restore the
matched backup, reload the restored service definition and start the restored
entrypoint; do not partially mix old generated messages with new Python code.

<a id="s2-incremental-acceptance"></a>

#### Incremental Acceptance

Run impacted static/profile/launch tests and adapter lifecycle mocks together
before the single CCS build. After migration, run one preflight and one idle
startup. Confirm physical package discovery, no active runtime references to
removed directories, matching ROS service/message MD5s, received UDP/heartbeat,
fresh chassis IMU, and a disabled bridge. Re-run only checks affected by a fix.

Hardware acceptance for this change is read-only: do not enable the bridge,
submit motion goals or repeat full mapping runs. Test reset rejection, stale
states, cancel/enable races, disable failure and emergency-stop acknowledgement
with mocks. Actual motion remains a separate operator-supervised acceptance.

The completed 2026-09-09 rollout is recorded in [VALIDATION.md](../../go2_robot2/VALIDATION.md).
It distinguishes actual device results from mock coverage and explicitly leaves
real motion acceptance and boot-time SNTP service retesting outstanding.

<a id="source-3"></a>

## 历史材料 3：deploy/go2_robot2/VALIDATION.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s3-go2-robot-2-部署与增量验证记录"></a>

### Go2 Robot 2 部署与增量验证记录

<a id="s3-验证范围与版本"></a>

#### 验证范围与版本

本记录对应 2026-09-09 的 `QRD_002`（`unitree@192.168.50.111`）部署。
工作空间为 `/home/unitree/ccs_edge_ws`，原生 underlay 为
`/home/unitree/go2_nav_ws`。代码基线为 `origin/main`：
`d597f0ed727bc7048268faf659432f93af3e0609`。

本轮部署版本：任务包 `0.5.1`、重定位包 `0.4.0`、Go2 集成包 `0.1.2`。
旧 `go2_edu` 和其他设备配置未更改；原生导航工作空间未修改、未重新编译。
所有 PID 和计数均为本次观测证据，不应作为后续停机时可直接复用的进程标识。

<a id="s3-已完成结果"></a>

#### 已完成结果

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

<a id="s3-udp-真机接收"></a>

##### UDP 真机接收

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

<a id="s3-控制状态与安全边界"></a>

##### 控制状态与安全边界

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

<a id="s3-旧状态说明"></a>

#### 旧状态说明

重启后，旧任务恢复时的 `PREPARE` 被 `LOCALIZATION_UNAVAILABLE` 拒绝，
因此保留为失败状态。这是新常驻链尚未完成新定位时的保护结果，不是本轮
`Trigger` 握手失败，也不能据此认为 UDP 修复无效。本轮未清理用户任务数据。

`ccs-sntp-sync.service` 的 `ExecStart` 已迁移到
`/home/unitree/ccs_edge_ws/scripts/ccs_sntp_sync.py`，并重新加载单元定义。
观察到的 `ActiveState=failed` 是既有历史状态：2026-09-08 22:29:06 启动，
22:31:28 报 `SNTP query failed: timed out`。本轮没有重启该设时服务，也没有
清除失败状态；正常启动与预检查的 SNTP 查询已通过，但这不等于完成了开机
授时服务实测，不应将历史失败误报成本轮新故障或宣称该服务已验证恢复。

<a id="s3-增量测试"></a>

#### 增量测试

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

<a id="s3-备份与载荷"></a>

#### 备份与载荷

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

<a id="s3-迁移与回滚用法"></a>

#### 迁移与回滚用法

迁移实现见 [migrate_workspace.sh](../../go2_robot2/migrate_workspace.sh)，日常启动和原生参数
对应关系见 [DEPLOYMENT.md](../../go2_robot2/DEPLOYMENT.md)。以下 `--check/--apply` 仅适用于
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

<a id="source-4"></a>

## 历史材料 4：deploy/EDGE_DEVICE_DEPLOYMENT_REQUEST.md

> 归档原文；以下命令、状态与结论按原日期理解。

<a id="s4-ccs-端侧部署总结与可复用任务指令"></a>

### CCS 端侧部署总结与可复用任务指令

用途：后续在其他设备上部署或迁移 CCS 端侧功能时，填写本文第二部分的参数区，将整个文件作为附件或文件引用发送给助手，并说“请按此文档执行部署”。本文不依赖原对话即可使用。

第一部分是 2026-09-09 Go2 部署的历史总结；第二部分才是新部署任务的执行指令。历史设备地址、版本、包数量、话题和验证计数不得自动成为新设备的配置或验收结果。本文不包含登录密码、密钥或令牌。

<a id="s4-一本次-go2-部署总结"></a>

#### 一、本次 Go2 部署总结

目标为 `QRD_002 / 192.168.50.111`，用户 `unitree`；CCS 工作空间为 `/home/unitree/ccs_edge_ws`，原生导航工作空间为 `/home/unitree/go2_nav_ws`，指控平台为 `192.168.50.101`。

| 问题 | 实施内容与经验 |
| --- | --- |
| `src` 中功能包依赖 `vendor` 软链接 | 逐个核验真实目标，备份并记录哈希，将 8 个功能包实体移入 `src`；成功构建并核验运行引用后删除活动 `vendor`。 |
| 启动链依赖 `bin/ccs_go2` | 阅读原生 README、启动指南及 launch 的 include 关系，根脚本直接启动功能 launch，显式传参，监控依赖和就绪；授时辅助程序迁入 `scripts`，更新 systemd 引用后删除活动 `bin`。 |
| 指控平台收不到可用 UDP 遥测 | 网络包已经到达，但精确的 8 项描述集合未被接收配置接受；仅追加相应契约并保留严格校验。仅该设备的遥测 IMU 改用 `/go2/imu`，算法仍使用 `/livox/imu`。 |
| 任务 reset 服务 MD5 不匹配 | 原生服务实际为 `std_srvs/Trigger`，客户端曾使用 `Empty`；改为正确类型并校验 reset 返回成功，随后才允许使能。 |
| 控制生命周期与版本不一致 | 端侧任务包由旧版统一升级为此次 `0.5.1`，消息与节点一致构建；补齐定位门控、计划时刻使能、状态新鲜度、取消竞态、停用确认、持久急停及显式人工恢复。 |
| 建图成果坐标系与重定位接入 | 保留该 Go2 的成果 `odom`、预览 `lio_odom` 约定，以专项配置对齐平台；同步 Go2 重定位及健康门控修复，集成包为 `0.1.2`、重定位包为 `0.4.0`。 |
| 其他设备兼容及后续发行 | 新增 `go2_robot2` 专项 profile，保留旧设备默认配置；更新发布清单、布局测试、部署记录和回滚说明。 |

此次按“本地差异合并及模拟验证 → 备份与物理迁移 → 一次 CCS 构建 → 空闲切换 → 真机无运动验收 → 最终清理 → 文档与 PR”的顺序完成。新根脚本有 9 项就绪的常驻 launch，建图、定位和导航由任务按需管理；原生导航算法未重新编译。

分组增量检查覆盖 UDP/发布/布局 38 项、任务控制 68 项、profile 10 项、互斥守卫 5 项、建图/重定位 73 项、顶层版本 5 项。另在隔离 ROS master 中验证了真实服务类型的握手和 reset 拒绝分支，未调用生产控制服务。

真机在指控平台的一次正常重启窗口内，用正式解析器和存储逻辑接收了 8 秒 UDP：一级遥测 161 包、二级 40 包、三级 8 包、心跳 8 包，窗口内无解析告警，IMU 与点云可用。后续正式平台仍出现旧序号包丢弃，截至当日 11:05:58 累计 60 次，原因尚未确定，不能宣称长期零丢包。授时查询通过，但开机授时单元的历史失败未重新验收。

实际执行了物理目录迁移、构建、启动、通信和接口验收；未执行真实使能、运动目标、重新建图或重定位。旧任务重启后可因尚未完成定位而被 `LOCALIZATION_UNAVAILABLE` 门控拒绝，不能把该状态等同于新的控制服务故障。

交付提交为 `59feb0d41c77dec151cf42ddfdd38c88fea0662d`，对应 [PR #38](https://github.com/AADCL/CCS_dev/pull/38)。这是历史参考；后续必须重新核对主线和 PR 状态，不能假设该提交始终是最新版本。

详细参考文件位于该提交的 `edge_side_pkg/deploy/go2_robot2/`，包括 `DEPLOYMENT.md`、`VALIDATION.md`、`start_ccs_edge_dev.sh`、`migrate_workspace.sh`、`verify_ros_contract.py` 和 `config/`。本机也可在独立工作树 `.codex-worktrees/go2-robot2-deployment-fixes/` 内查阅。历史备份为 `/home/unitree/ccs_edge_ws/backups/go2-physical-20260909T024613Z`，只用于这台原设备的成套恢复，不是新设备的安装输入。

<a id="s4-二可直接发送的部署指令"></a>

#### 二、可直接发送的部署指令

请依据以下参数，在指定目标设备上完成 CCS 端侧功能部署或迁移。读取代码和实机后直接实施，完成本地源码同步、必要构建、增量验证、部署记录，以及参数区指定的 Git 交付。不要只停留在方案说明。

<a id="s4-1-目标参数与执行范围"></a>

##### 1. 目标参数与执行范围

请在发送本文前替换必填占位符。可自动发现的字段可以保留“读取实机确认”，助手应优先使用已有会话信息和只读检查；只有无法安全推断的必要信息才集中询问。

```yaml
设备名称: "<必填，例如：第二台 Go2、Scout Mini、Wheeltec>"
设备类型: "<必填，例如：QRD、UGV、AGV；与平台定义一致>"
设备ID: "<必填，新设备唯一 ID，不能复用参考设备 ID>"
SSH地址: "<必填，目标 IP 或主机名>"
SSH用户: "<必填>"
SSH端口: 22
SSH认证: "复用当前会话已提供的认证；否则另行提供，不写入本文"
CCS端侧工作空间: "<必填绝对路径，例如 /home/用户名/ccs_edge_ws>"
原生功能工作空间: "读取实机确认；已知时填写绝对路径"
指控平台地址: "<必填>"
指控平台工作目录: "使用本会话本地项目；多实例时核实实际入口和数据目录"
ROS版本与系统架构: "读取实机确认"
LAN与SDK或DDS接口: "读取实机确认；同型号也不可沿用旧设备值"
地图根目录与外参: "读取原生配置及已有地图目录确认"
新部署profile: "<建议 品牌_型号_编号，独立于其他设备>"
本地仓库: "本会话当前项目；核对已有 origin"
目标基分支: "main，开始时读取最新 origin/main"
Git交付: "提交、推送独立 codex/ 分支、创建面向目标基分支的 PR；不合并"
部署信息发布范围: "<请选择：允许设备ID/IP/路径/验收细节进入指定origin；或仅允许脱敏发布>"
切换窗口: "允许在确认相关设备及平台任务空闲后统一切换一次，并在必要时正常重启平台一次"
真机验证范围: "默认无运动验证；不真实使能、不发送运动目标、不重新完成整轮建图或重定位"
额外功能或约束: "<没有则填无；如相机型号、视频降级、指定原生算法等>"
```

发布范围仅授权向参数区核实的仓库交付本次内容；任何情况下都不包含密码、密钥或令牌。若改为只交付本地文件，应相应修改 `Git交付`。已在当前会话明确授权且范围一致的事项无需重复确认。

<a id="s4-2-完成目标与适配原则"></a>

##### 2. 完成目标与适配原则

1. 端侧只部署所需 ROS 功能包、配置、启动辅助程序和说明。若已有指控平台副本，核验其边界、依赖及需保留数据后备份剥离；不把整套指控平台作为端侧运行依赖。
2. 需要安装的 CCS 功能包以真实目录存放于 `<CCS工作空间>/src`，根目录提供可直接使用的 `start_ccs_edge_dev.sh` 和只读 `--check`。不依赖历史 `vendor` 包软链接或 `bin` 总控入口。
3. 完成设备身份、MQTT 状态/心跳、UDP 遥测、按硬件提供的视频、建图与成果、地图下发、重定位、任务控制和急停接口的适配。缺少硬件或原生能力时记录具体缺口与降级状态，不伪造支持或就绪。
4. 优先复用已验证的包和框架，通过新 profile、后端或明确参数加入设备差异。共享代码修改必须保持其他设备原默认行为，并验证受影响的旧设备契约。
5. 本次端侧有效修复必须同步到本地代码；对端侧已有但尚未入库的改动先比较、理解并按差异合并。保留用户未提交的工作，避免整包覆盖其修改。
6. 参考 Go2 的流程和检查方法，不硬编码其包数量、版本、launch 数量、地址、接口、话题、坐标系或服务类型。本仓库现有交付基于 ROS1；发现 ROS2 或其他不兼容环境时先明确适配缺口，不能假定 Noetic 脚本可直接运行。

<a id="s4-3-阶段-a只读盘点与确定接口"></a>

##### 3. 阶段 A：只读盘点与确定接口

读取本地规则、仓库状态、已有设备 profile 和目标设备原生工作空间中的 README、启动指南、package/CMake、launch、配置及必要实现，形成以下盘点后再编辑：

- 身份与环境：实际登录用户、设备 ID/IP、网卡、路由、ROS 环境、系统架构、磁盘空间、权限与当前授时状态。
- 源码与构建：所需包名和真实路径、软链接目标及所有者、版本/提交、修改差异、ROS underlay/overlay 顺序和既有构建缓存。
- 运行关系：进程与启动入口归属、systemd、ROS 节点/话题/服务/action、订阅发布关系、端口占用及活动任务。区分当前活动执行与历史持久化状态。
- 原生 launch：展开 include，记录每项功能入口、必需参数、默认参数、内含驱动/底盘/算法，以及常驻和按需启动边界。
- 数据与控制：设备 ID、地图 ID/根目录、外参、位姿/点云/IMU/图像来源、各阶段 frame、保存服务、控制服务及状态反馈。

原生文档与实机不一致时，以核实后的代码、接口和实际配置为依据，并记录差异。确认原生功能包已经可用，避免把“找到了包目录”当作“功能已部署”。

<a id="s4-4-阶段-b本地实现与独立配置"></a>

##### 4. 阶段 B：本地实现与独立配置

在 `edge_side_pkg/deploy/<新profile>/` 建立专项配置、启动脚本和部署说明；为确有需要的集成功能建立最小包或适配入口，更新发布清单。功能包清单由设备能力决定。

根脚本按依赖顺序加载 ROS、原生 underlay、CCS overlay，验证路径和配置，并直接调用功能 launch。明确传入设备配置、平台地址、网络接口、外参、地图参数和输出路径，不依赖交互 shell 的偶然环境。脚本使用 Linux 换行，并设置需要的可执行位。

根据实际设备决定常驻的传感器驱动、底盘接入和通信服务；建图、定位、导航由指控任务按需启动。检查原生总 launch 是否已包含常驻组件，避免重复启动驱动、底盘桥接、FAST-LIO 等节点；互斥冲突须返回明确原因。等待依赖时检查进程存活、服务或新鲜数据，不能仅凭节点名判断整项功能就绪。

`--check` 只检查配置、launch 解析、接口前提与时间偏差，不启动或停止节点，不使能、不调整系统时钟。正常退出时先停止任务消费，按设备接口完成必要停用，再逆序清理本流程拥有的进程；对自建 ROS master 和复用 master 分别处理，不能清理未知进程。

需要授时时，将辅助程序放在 `scripts` 并更新实际服务引用。区分只读查询和会设置时钟的服务，不在任务运行中步进时间。SSH 非 TTY 场景应实测 sudo 认证的作用域，必要时在同一个 Bash 中认证并执行迁移，凭据不进入文件或日志。

<a id="s4-5-阶段-c通信建图和任务契约"></a>

##### 5. 阶段 C：通信、建图和任务契约

**UDP 与其他通信：**

- 核对设备实际目的 IP/端口、平台绑定、设备身份和数据来源；按“传感器有效数据 → 端侧发送 → 平台正式解析与存储”逐段定位。检查 MQTT 心跳及硬件具备时的视频链路。
- 使用项目正式解析器验证精确遥测描述集合及哈希。需要新集合时，在开发和发行配置追加，保留旧集合，不全局放宽校验或修改协议绕过问题。显示名等描述字段也可能影响哈希，必须依据实际编码实现计算。
- 检查 IMU 四元数、消息类型、数据新鲜度和时间戳。遥测与算法可以使用不同来源，但应有实测依据且只改变目标设备配置。
- 验证各级遥测与心跳均被接受，记录窗口、包数和告警。接收到网络包不等于业务数据可用；短时无告警不等于长期无丢包。区分序号按设备/会话/消息类型/级别的隔离，保留乱序保护。

**建图、成果与重定位：**

- 核对雷达/IMU输入、算法输出、预览位姿与点云、积累地图和保存服务；明确真实 frame 与变换来源，按设备配置对齐平台预期，不能直接改成果标签或伪造 TF 来通过校验。
- 核对地图文件类型、清单与哈希、地图 ID、落盘目录、导出参数，以及结束建图后的成果保存流程。启动等待应针对实际新鲜输出；检查重复请求处理和超时是否会误触发多份栈。
- 地图下发和重定位按该设备原生能力启用，验证初始位姿、状态文件、定位健康反馈与 TF。原生节点在定位成功前发布的初始 TF，不能单独作为定位成功或可执行依据。

**任务与控制：**

- 对每个 reset/enable/disable/cancel/stop 服务，核实实际类型、ROS1 MD5、请求字段、返回语义和失败原因。不能把所有设备的 reset 一律改成 `Trigger`；Go2 的修复只说明必须匹配真实接口。
- 节点、消息、依赖及配置采用兼容的一组版本并一致构建。准备任务时验证地图和定位；计划执行时再使能，reset 必须成功；取消、完成、失败和急停均要处理零速与停用，并确认结果后报告终态。
- 验证状态新鲜度、定位失效、服务超时、取消与使能并发、延迟响应补偿、旧执行回调和新执行隔离。长控制操作应持续反馈状态，避免协调器误判反馈超时。
- 锁存 Bool 只能证明某次状态；应采用该设备可靠的周期反馈及必要的一致性检查，不能使用旧值确认当前停用。停用失败须保留原因，不能虚报成功。
- 采用该设备对应的持久急停机制，重启或新任务不得自动解除。显式人工清除只能在无活动执行且确认已停用时进行，清除本身不使能。Go2 私有 Trigger 服务名只作参考，其他设备按本地接口适配。

<a id="s4-6-阶段-d备份迁移与必要构建"></a>

##### 6. 阶段 D：备份、迁移与必要构建

先在本地完成受影响的静态检查和隔离 mock。准备并核对待部署文件清单与哈希；在确认切换窗口、目标身份、相关任务空闲、进程归属后开始实际迁移。

备份范围包括旧源码及链接目标、配置、启动脚本、授时单元、构建状态/生成消息和版本清单；记录绝对备份路径、校验值及恢复命令。备份不能放在待删除的 `vendor/bin` 中。核实可恢复性后再变更。

逐个处理要迁移的包：验证软链接目标确属本次管理范围，将 workspace 内 `vendor` 中的包实体移到同名 `src` 目录，逐包对账。若目标位于原生工作空间或共享目录，不得把原生包搬走或删除，应按依赖与所有权调整安装方案。源码已经是实体目录的设备跳过此步；没有 `bin` 依赖的设备无需制造一次额外迁移。

部署本地合并后的源代码与配置，处理路径迁移引起的旧构建缓存失效，选择与当前工作空间匹配的构建工具及并发参数，集中完成一次必要的 CCS 构建。原生算法无需改动时不重新编译；构建失败修复后只重建受影响内容，不能为了“一次构建”省略必要验证。

构建后检查 ROS 包解析到真实 `src`、生成消息/服务契约正确、launch 可解析，确认活动配置、脚本、构建结果和 systemd 不再引用旧 `vendor/bin`。运行验收通过后才删除这些精确的废弃目录，并告知备份与恢复方式。

同一设备迁移保留地图、任务、日志和安全状态；迁移到另一台设备时只迁移经过适配的代码和配置，不把原设备 ID、活动执行记录、旧 PID、急停状态文件或地图绑定当成新设备默认值。目标设备已有的安全状态必须保留；地图跨设备传输需通过明确的地图下发流程。

参考 Go2 的 `migrate_workspace.sh` 绑定了特定用户、IP、包清单和绝对路径，不能直接在另一台设备执行。需要迁移脚本时，先为目标设备适配和检查其 `check/apply/finalize/rollback` 行为，再运行。

<a id="s4-7-阶段-e增量验收与统一切换"></a>

##### 7. 阶段 E：增量验收与统一切换

先记录已有失败和环境基线，建立“改动 → 对应检查 → 实测结果”清单。集中运行受影响的配置/launch/脚本解析、服务与生命周期 mock、UDP 契约、旧设备默认行为及发行清单检查；测试通过后不反复重跑整套。出现新修改或失败时，仅补跑直接受影响检查及必要依赖。

mock 覆盖 reset 拒绝、使能超时、陈旧反馈、定位失效、取消竞态、停用失败、急停确认与人工恢复，以及完整任务状态流。涉及 ROS 服务握手时，使用独立可用的 master/端口和专用测试命名空间；验证没有调用生产控制服务。选择模拟底盘并不意味着其他传感器也已模拟，需核实隔离边界。

确认空闲后统一启动新的端侧入口。若平台需要重载数据契约，在已授权窗口正常重启一次，沿用它原来的工作目录、入口和数据目录。验证端口与进程归属；不启动另一个空数据目录的平台实例代替原实例。

真机默认验证物理包路径、启动就绪、有效输入、实际 UDP/心跳、可用的视频、服务类型、只读状态和底盘停用，不发送真实运动控制。按约定窗口收集数据，明确记录尚未启动的按需算法；空闲时无定位输出不能直接判定驱动故障。不得把 mock 状态流或接口存在表述成真实任务、制动、建图或重定位验收通过。

遇到阻断性的部署失败，先保留证据，只停止本次自有流程，在空闲状态下恢复匹配的源码、生成消息、配置和启动文件，恢复服务引用并验证。回滚也需保留目标地图、任务与安全状态，不得混用旧消息和新节点。

<a id="s4-8-阶段-f文档发布与最终交付"></a>

##### 8. 阶段 F：文档、发布与最终交付

在 `edge_side_pkg/deploy/<新profile>/` 保存 `README.md`、`DEPLOYMENT.md`、`VALIDATION.md` 或项目同等文档，至少记录：

- 根因、原生入口和参数对应表、常驻/按需关系、物理目录、设备差异与适用范围。
- 本地和端侧版本来源、实际部署文件清单、备份与载荷哈希、迁移及可执行的回滚步骤。
- 逐项实测结果、测试命令/摘要、实际构建与重启次数、采样时间、已知残留问题及未做的真机验收。
- 日常预检查、启动、正常停止、日志查看和人工急停恢复说明；其中真实控制类命令只作明确说明，不在无运动验收中执行。

验证新增功能包、profile、脚本与说明均进入发布产物；按项目需要更新版本、清单、变更说明和受影响的布局测试。

按参数区的 Git 交付要求，核对最新主线，在独立 `codex/` 分支或工作树完成本次提交，保留用户原工作树；逐项检查提交内容，不夹带设备运行数据、凭据或无关修改。推送前按填写的发布范围处理部署细节，创建或更新对应 PR，正文说明具体问题、最终行为、兼容性、增量证据及回滚方式。核对远端提交、目标分支与 PR 状态，不自动合并。

最终答复给出：已部署内容、当前端侧状态、启动入口、本地文档位置、关键验证结果、剩余限制、备份/回滚位置，以及已要求的提交号和 PR 链接。任何未完成项应说明具体原因，不把计划或历史记录写成此次实际结果。

---

下一次使用时，只需补齐上面的目标参数，并将本文件发送到对应项目会话：

> 请按附件《CCS 端侧部署总结与可复用任务指令》执行。参数区描述的是本次新目标，第一部分的 Go2 数据仅作经验参考。请完成实现、部署、增量验证和已选择的交付内容。

## 后续记录填写格式

追加日期/范围、源码版本与差异、文件清单及前后哈希、备份/证据路径、命令与实测、告警/未测项、启停/回滚。回滚不覆盖更新的急停状态，文档整理不表示重新部署。

<a id="camera-emergency-20260911"></a>

## 2026-09-11：同步 GO2_3 相机启动与急停修复

### 范围与实测原因

本次仅针对 QRD_002 / 192.168.50.111 同步相机启动和急停恢复代码，不修改七份 YAML、原生工作空间、地图、任务、安全状态或授时服务。工作树基线 HEAD：`c0243faeb08e8e98fe765a9d65166915bda282d2`（未提交的增量以下表 SHA-256 为准）；公共任务源码沿用本地当前版本，更新目标机尚缺的三个 Python 模块，无 catkin 重建。

- 旧根脚本使用 `device_type:=d435i`，注册相机节点即报告 ready；直到任务消费已启动后，才以一次 `rostopic echo -n 1` 检查 RGB，既不检查连续帧也不检查时间戳。该流程与 GO2_3 的旧问题相同；本机旧脚本没有 serial_no 下划线参数，不能把 GO2_3 序列号错误当成本机实测根因。
- 只读盘点时相机驱动已启动、USB 拓扑为 5000M（USB3），不是 GO2_3 的 USB2.1。保留 RGB 640×480@30；旧 camera.log 有 libusb resource temporarily unavailable 警告，不能宣称硬件或 USB 告警已修复。
- 盘点时持久急停原因是 `navigation unloaded; stop: control transition did not finish before disarm`，旧根 PID 18391 与大部分 launch 共用 PGID 18391，存在终端 Ctrl+C 并行终止 master/适配器的退出竞态。当前安装的任务接收端也缺少协商/提交锁存检查和停止锁存重试逻辑，导致轨迹保存后失败且每5秒重复准备。
- 计划停机复查时，原根进程已先行退出，`/proc/18391` 不存在，本流程未向该 PID 发停止信号；现场已启动另一原生 navigation.launch（地图 lab_202609101805，PID 34553、master 34650、底盘 bridge 34762）。此时标记更新为 `control disable failed: control service timed out: /go2_sdk_bridge_real/enable`。用户明确选择“保留原生导航，仅安装并做隔离测试”。最新只读诊断显示原生底盘 motion enabled、commanded_vx/wz 为零；本流程没有发送生产 enable、disable、reset 或运动目标，不能将该时段记作底盘持续 disabled 验收。

### 实现

1. 默认单设备自动选择，不传 device_type。可选 `CCS_D435_SERIAL` 原样传给 serial_no，不添加下划线。保留 RGB 640×480@30，关闭深度、红外、gyro、accel、TF。
2. 新增工作空间 scripts/ccs_ros_readiness.py：camera 模式只订阅 `/camera/color/image_raw`（sensor_msgs/Image）。节点注册后等最多30秒，至少两帧、时间戳递增、年龄≤3秒才显示 `[OK] camera is ready.`，然后启动 SRT。inputs 模式只检查其他核心传感器。失败回放诊断并指向实际 camera.log，执行安全清理。
3. 核心输入及新鲜 disabled 检查先于任务消费；roscore/roslaunch 采用 setsid，保持父子 PID 可验证。清理先任务消费及适配器、再底盘停用确认、其余组件逆序、最后仅自有 master。监控读取完整 ROS 节点快照，查询暂时失败重试三次，真实进程退出仍报错。
4. 同步 node.py、control_safety.py、scout_adapter.py：已知/损坏急停标记在状态协商中报告失败，在 prepare/commit/execute 拒绝；锁存失败不自动准备重试，定位暂不可用仍可重试。只在 ROS shutdown 已请求且新鲜状态确认 disabled 的退出分支避免重复停用 RPC；普通停用、真实停用失败及持久锁存规则保留。
5. Robot2 的日志仍在 `/home/unitree/ccs_edge_ws/logs/managed`，新增 startup.log 记录启动/清理。此次不改为 Robot3 的按次目录，也不改 SNTP/MQTT 参数。--check 不运行 readiness 观察器，不启停 ROS，不写启动日志/PID/急停状态。

### 备份与运行文件哈希

备份根：`/home/unitree/ccs_edge_ws/backups/go2-robot2-camera-emergency-20260911T082429Z`。
证据与增量清单：`/home/unitree/ccs_edge_ws/run/go2-robot2-camera-emergency-20260911T082429Z`。
备份含受影响原文件、原日志、docs/go2_robot2、原急停/定位状态和进程快照；安全状态备份仅作证据，不用于覆盖最新标记。只同步下列5个运行文件，脚本0755，Python模块0644，全部 LF 无 BOM；写入前后逐一核对哈希，安装后用目标 Python3.8做 AST 检查。

| 工作空间相对路径 | 安装前 SHA-256 | 安装后 SHA-256 |
| --- | --- | --- |
| `start_ccs_edge_dev.sh` | `492e66eaa98dede275fa236a6b00518f6cc7b6043b2c548144f178fc5b1999e3` | `f7702b1705b12e3ea56f15ea0a20ead152020372a5794f613e85178e77e7aa3a` |
| `scripts/ccs_ros_readiness.py` | `新增` | `ef6338e34b45627b9cb7db5fff9cde01482965aa8a1fa7e6a425522969e3482b` |
| `src/EPGeneral_task_control/src/epgeneral_task_control/node.py` | `bf7356ae5d8dca8f3e74faabf79d52805b41a3e49319f3037dedcf7022a5d2e8` | `12770eb26c844a7efd726e30f0aced5e63a3bf8264796807fc1bb000682c46cc` |
| `src/EPGeneral_task_control/src/epgeneral_task_control/control_safety.py` | `706eba693bb1deca2921effb4334111c4f263020b0349d2bd2419cfd9b0294cc` | `4f4b3147291de764d418abff4102b0a8b1daefa21e781446b5f1969372de29f4` |
| `src/EPGeneral_task_control/src/epgeneral_task_control/scout_adapter.py` | `85b858179cb8f042f7d5e8c8656224c04f0ffb27ad39ed7f3e5d143f6d7c00be` | `88034ff73c617a6c33262a83839add74cc9ec0bf5af941d11ca5a4925585066e` |

安装时保留的最新急停文件 SHA-256 为 `97f4d173bbeb308234e5ca709fcb6e39591a8bef8b5705aa95169d8670aff5b1`；与安装后、隔离测试后相同。本机早期盘点标记 SHA-256 为 `f9fe084e0109bd42eea16f931f9b44590b4125bcdd4c0c7e01db6612e64051da`，两者差异发生在旧入口退出与原生导航接管期间，不是本流程清锁。

### 增量验证及限制

- 文档与发布内容10项通过；本地/目标 bash -n、LF/无 BOM、git diff --check 通过。
- 本地：Go2Robot2 profile 13项、任务安全44项、任务接收17项通过；覆盖相机参数/帧判断/门控顺序，以及锁存拒绝、传输中锁存、损坏标记、不可重试锁存与可重试定位失败、复位约束和退出分支。仅运行受影响测试。
- 目标 Linux：复用通用 watchdog 10项测试（GO2_ROBOT3_STARTUP 指向 Robot2 脚本），包含真实进程的终端 SIGINT、父进程 TERM、独立会话存活和清理顺序；均通过，未涉及生产底盘。
- 目标 `./start_ccs_edge_dev.sh --check` 返回0，仅输出：
  `[OK] GO2 configuration, time and persistent launch files passed preflight; no nodes were started.`
  前后生产 master PID、原生 launch/bridge PID及进程启动时刻、ROS图、启动日志/PID文件与急停文件一致。
- 隔离 master `http://127.0.0.1:11321`、命名空间 /ccs_probe：Trigger reset / SetBool enable、应答拒绝及停用握手通过；模拟 enable true=1、enable false=1、reset=2，生产服务调用=0。
- 隔离 RGB：紧接控制契约测试时11321端口绑定检查拒绝复用，未启动任何观察节点；改用独立11322端口。模拟640×480帧按30Hz循环发送，递增时间戳返回0（Fresh camera confirmed），冻结时间戳返回1（Fresh camera not confirmed），符合预期。该项不是物理相机帧率实测。
- 测试结束后两个隔离端口无监听，生产 ROS图与 PID 34553/34650/34762 未变，生产 reset 调用=0。正常 CCS 启动、真实 RGB 30帧帧率、SRT画面、生产急停复位、真实有序退出重启未执行。未进行全量测试、catkin重建、建图/定位/导航或运动验收。

### 下次切换与恢复

保持原生导航运行期间不要并行启动 CCS 根脚本。操作人员正常结束其拥有的原生导航并确认节点释放后，从 `/home/unitree/ccs_edge_ws` 执行 `./start_ccs_edge_dev.sh --check`，通过后运行 `./start_ccs_edge_dev.sh`。相机输出就绪应晚于新鲜帧确认；SRT应在其后启动。遇到缺帧查看 `logs/managed/camera.log`；急停看 `logs/managed/task_control.log`、`startup.log` 与 `run/state/go2_task_safety.json`。

现有急停**仍锁存**。修复根因、确认无准备/执行或控制过渡且底盘持续 disabled 后，由操作人员按使用手册调用 `/epgeneral_navigation_task_adapter/reset_emergency_stop`（std_srvs/Trigger）一次，核对 success 和新鲜 disabled，再重新下发任务。不得直接删除标记，不能用重启或新任务清锁。本次不声称任务已可实际执行。

回滚仅恢复本次5个代码/助手文件：先停止使用这些文件的 CCS 入口，逐一核对当前文件仍匹配上表安装后哈希，再由备份恢复旧4文件并校验安装前哈希；新增 readiness 助手仅在仍匹配本次哈希时移除。保留最新任务/地图/急停/定位状态、原生导航和所有诊断证据，不整目录回滚工作空间。远端附加文档为 `docs/go2_robot2/INCREMENT_20260911_CAMERA_EMERGENCY.md`，清单为同目录 `INCREMENT_20260911_CAMERA_EMERGENCY.json`；历史文档保留。


<a id="joint-shutdown-20260912"></a>
## 2026-09-12 联合下发拒绝与退出急停修复（已部署）

### 根因与变更边界

历史标记为 2026-09-11 16:15:35 的 control disable failed / control service timed out。9 月 11 日按当时授权仅安装并保留原生导航，未做生产复位，正常重启仍保留该锁存。 10:03 的联合 task_prepare 只读取并拒绝已有急停标记，没有调用底盘控制服务，因此不能归因于同时下发造成停用超时。另经模拟复现：协调器的内部 shutdown-unload 可先于适配器关闭信号到达，造成重复停用；旧 disabled 快捷确认未检查尚未完成的 RPC。

本轮用户已明确授权受控重启、每台一次服务复位和仅下发验收。安装前确认任务 failed、无建图/地图生成、底盘 disabled 且运动指令为零，正常停止已识别 CCS 根入口。两台安装同版 control_safety.py 和 scout_adapter.py：内部 UNLOAD 与 close 共用幂等关闭，先阻止新准备/调度/复位并停止监控，再串行取消目标、零速、停用和清理自有导航；重复入口保留首次结果，失败不能被覆盖。关闭期间拒绝 PREPARE/SCHEDULE/复位；快捷 disabled 同时排除控制过渡与 RPC，真实停用失败仍锁存，普通 STOP/UNLOAD 仍严格确认。

未改变 ROS 契约、YAML、任务协议、运动策略、相机、授时或日志策略。Go2_3 本次同步了适配器关闭修复，不代表其他尚未部署的根脚本/存储改动已发布。未提交、推送、创建 PR 或重建 catkin。

### 备份与源码身份

- 源码基线 HEAD：`c0243faeb08e8e98fe765a9d65166915bda282d2` 加本轮未提交工作树修改；以以下目标字节哈希为准。
- 部署前代码、标记原文、配置、任务、日志、进程证据及 before.json：`/home/unitree/ccs_edge_ws/backups/joint-task-shutdown-20260912T022612Z`。
- 本轮阶段及验收证据：`/home/unitree/ccs_edge_ws/run/joint-task-shutdown-20260912T022612Z`；其中 safety-before-install.json 保留旧入口退出后的标记。部署前标记 SHA-256：`97f4d173bbeb308234e5ca709fcb6e39591a8bef8b5705aa95169d8670aff5b1`。备份中的安全文件仅作证据，不用于回滚。
- 目标实际导入路径：`/home/unitree/ccs_edge_ws/src/EPGeneral_task_control/src/epgeneral_task_control/`，实体源码目录。运行模块 LF、0644，根脚本权限保留。

| 文件 | 部署前 SHA-256 | 部署后 SHA-256（两台一致） |
| --- | --- | --- |
| control_safety.py | `4f4b3147291de764d418abff4102b0a8b1daefa21e781446b5f1969372de29f4` | `37a099a8662e001d7a0b360c7661697b8412336739ae0ab327fd939a88a692bc` |
| scout_adapter.py | `88034ff73c617a6c33262a83839add74cc9ec0bf5af941d11ca5a4925585066e` | `fb10efa86ae745f33c06bbb78e2fa05488f4ff1eded1e628e93b301d75393b51` |

### 增量验收实测

- 本地任务安全 51、适配器 13、接收端 17 项通过；两个 Go2 profile 共 33 项通过。覆盖 pending RPC、晚到 enable 补偿、过期状态、拒绝/超时锁存、内部卸载先到、ROS 关闭先到、重复/并发 close、首次失败保留、关闭期间 STOP/watchdog/旧回调与新工作门控。既有锁存拒绝 prepare/commit、传输中锁存、损坏标记、人工复位约束、复位后下发及定位失败可重试继续通过。
- 文档/发行内容10项通过；两台 profile 的 bash -n、Python语法、LF/无 BOM、运行模块 SHA-256 与 git diff --check 通过。仅运行受影响检查。
- 每台目标 ARM Linux：81 项任务测试、10 项真实进程 watchdog/信号测试通过；独立 master 127.0.0.1:11331 运行真实任务双节点 launch，覆盖内部 unload 后 SIGINT、无 unload 的 SIGTERM 两例，均无新标记，关闭停用 RPC=0、生产服务调用=0，测试进程和 master 全部清理。初次测试包缺少 fixture/目录布局，补齐后重新执行通过；不属于生产运行失败。
- 安装后 --check 返回0；运行中再次只读检查，前后 13 个生产节点及 PID 完全相同，latest 不变。原精简提示保持。正常入口均到达最终 services are running；相机新鲜帧及底盘 disabled 检查通过。
- 每台仅调用一次 `/epgeneral_navigation_task_adapter/reset_emergency_stop`（std_srvs/Trigger），返回 `success: True`、`emergency stop cleared; control remains disabled`；未直接删除文件。随后18条连续诊断覆盖约17秒（至少三个5秒周期），新鲜 disabled、运动指令为零，标记解除。
- 本设备根入口以 SIGINT 正常退出，28 个记录的自有进程全部退出，自有 master 退出，未生成新锁存；重新启动后仍无锁存。终止任务优先、底盘停用、逆序清理、master 最后退出的脚本/进程回归和实机检查通过。
- 联合验收使用原 task `6b482cb3119944be9a166e661052b473`，本设备子任务 `74acbf30c1594323b92474250bc76ebc`，revision=5，4个原航点，CRC32=3794634031；未修改地图或路径。两台在约0.6秒内完成全部 prepare/chunk/commit 发送，端侧 prepare/commit 均 accepted=True，并记录 `trajectory XML committed revision=5 waypoints=4`。请求 ID：prepare `joint-final-7ab3a51c70334c4cae899b69f1e058f0`，commit `joint-final-55d05906355d440196ed26f6413975eb`。
- 首轮人为分步读日志超过10秒传输期限，仅证实幂等 commit 接受；未将其当作完整传输通过。随后以上新请求完成真实 XML 落盘，最终证据为 joint-delivery-complete-result.json。再次18条诊断覆盖约17秒，仍 disabled、零指令、无急停标记。
- 最终导航准备反馈是 `LOCALIZATION_UNAVAILABLE` / `Scout is not localized on a usable map`，定位文件为 standby，任务状态 failed。此为重启后的定位门控，保留可恢复错误的5秒准备重试；不等于仍被急停拒绝。下发成功不代表 navigation ready。本轮没有 execute、SCHEDULE、运动目标、整轮定位/建图或联合运动验收，也未做全量测试。

### 最终运行、日志与回滚

- 最终保留 CCS 根入口 PID `73778` 运行（仅记录当时身份，后续操作必须重新核对 PID/命令行/归属），底盘 disabled。正式执行前须先完成定位，再按正常操作重新下发/执行；不得为消除 failed 绕过定位或安全门控。
- 当前组件日志：`/home/unitree/ccs_edge_ws/logs/managed/`；本轮启动输出：`/home/unitree/ccs_edge_ws/run/joint-task-shutdown-20260912T022612Z/start-final.log`，前次退出日志归档 first-start-logs。两台原日志布局差异保留。
- 启动：`cd /home/unitree/ccs_edge_ws && ./start_ccs_edge_dev.sh`；只读检查加 `--check`。停止：前台 Ctrl+C，或核对 `run/managed/startup.pid` 对应根脚本后 `kill -TERM <核实的PID>`；不要直接杀适配器/bridge/master。
- 端侧本轮文档/清单：`docs/go2_robot2/INCREMENT_20260912_JOINT_SHUTDOWN.md`、同名 `.json`；历史记录保留。文件清单记录文档与运行代码哈希，deployment_source.json 追加本轮条目。
- 回滚：确认空闲后正常停止该 CCS 根入口，先核对当前两模块仍匹配本表部署后哈希，再仅恢复备份中对应两模块及匹配的文档，校验部署前哈希和 LF/权限，--check 后启动。保留最新任务、定位、安全状态和所有诊断；不恢复旧急停文件、不清锁、不整目录覆盖工作空间。旧代码含本次退出竞态，回滚后的急停必须重新诊断，不能自动复位。


<a id="field-confirmation-20260912"></a>
## 2026-09-12 后续现场确认与经验归档

用户在本次会话明确反馈：“落地测试已完成”，并要求归档本轮修改与经验。记录为两台退出急停与联合下发修复的后续现场确认；未另提供测试次数、运动范围、轨迹、速度或逐项原始日志，因此不补写这些指标，也不将其标成工具重新执行的验收。

此前“联合传输已落盘、工具验收未发送 execute/SCHEDULE、disabled/standby”的结果与时间边界原样保留，不再当成现场后续测试后的实时状态。历史 INCREMENT_20260912_JOINT_SHUTDOWN.json 及其 SHA-256 不改写；后续卸载后恢复准备的源码修订仍只有本地测试记录，不能由此次总体反馈推断已部署。

复用规则已归档到 [GO2 部署经验](../../../documents/GO2_DEPLOYMENT_LESSONS.md)，并同步更新从零部署指南、接口参考、使用手册、任务包 README 与部署请求模板：先按 recorded_at/reason 查锁存，统一幂等收尾并排除未完成 RPC，区分卸载复用和永久关闭，在传输时限内连续完成联合下发并检查本轮 XML 落盘，按证据来源记录现场确认。

本次是本地文档整理，没有重新连接、启停或改变设备运行参数。端侧历史文档及清单保持；下一次交付按部署指南的离线目录层级同步本次文档，再生成新的文档清单，不覆盖旧验收证据。
