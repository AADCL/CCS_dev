# 端侧从零部署指南

适用：CCS 0.24.0 当前源码；维护日期：2026-09-12。本指南用于没有前次聊天上下文的新部署。包版本见[总览](../README.md)，接口填写见[话题与服务清单](CONFIG_TOPIC_REFERENCE.md)和[完整参数参考](INTERFACE_REFERENCE.md)，日常操作见[使用手册](USER_MANUAL.md)。

设备历史集中在 [deploy/records](../deploy/README.md)。历史 IP、标定、PID、哈希、告警及“最终运行”只代表当次事实。QRD_002/QRD_003 的适配器退出修复已于 2026-09-12 部署；QRD_003 的建图存储异常和重复启动锁修复仍只有本地验证。本轮 PR 审查新增的普通卸载后重新准备修复尚未部署，真实 close 或 ROS 退出仍阻止新任务。每次交付重新记录源码版本、工作树差异和端侧 SHA-256，不用较早记录证明后续修订已部署。

故障判断与复用规则见 [GO2 部署经验](GO2_DEPLOYMENT_LESSONS.md)。2026-09-12 用户已确认两台本轮修复的落地测试完成，具体范围按设备记录追加；该确认不改变尚未部署修订的状态。

## 1. 明确部署对象

填写[部署请求模板](../deploy/EDGE_DEVICE_DEPLOYMENT_REQUEST.md)：设备 ID/显示名、账号/IP、平台地址、硬件、underlay/overlay、允许的验收范围。设备 ID 必须唯一，profile 是运行配置名称，二者不同。新设备另建 profile 和 `deploy/records/<设备ID>/DEPLOYMENT.md`，不覆盖旧设备。

| ID | profile | 已有端侧 | CCS 工作空间 | 原生 underlay |
| --- | --- | --- | --- | --- |
| QRD_001 | go2_edu | nvidia@192.168.50.100 | /home/nvidia/ccs_edge_ws | /home/nvidia/go2_mid360_nav/catkin_ws |
| QRD_002 | go2_robot2 | unitree@192.168.50.111 | /home/unitree/ccs_edge_ws | /home/unitree/go2_nav_ws |
| QRD_003 | go2_robot3 | unitree@192.168.50.112 | /home/unitree/ccs_edge_ws | /home/unitree/go2_nav_ws |
| UGV_001 | scout_mini | nvidia@192.168.50.120 | /home/nvidia/ccs_edge_ws | RealSense → Scout → livox_fastlio，见接口参考 |
| UGV_003 | wheeltec_r550p | nrc19@192.168.50.122 | /home/nrc19/ccs_edge_ws | /home/nrc19/livox_fastlio |
| AGV_001 | ground_air_agv | bitcq@192.168.50.130 | /home/bitcq/ccs_edge_ws | /home/bitcq/catkin_ws |

以上是实例，不是新设备默认值。原生 Go2 优先参考 go2_robot3 的修复，但其预检仍含 QRD_003、网卡、地址及路径约束；复制后不能只改 device.yaml 或 SSH 地址。不得复制其他设备的 mission、地图绑定、锁或急停状态。密码不写入仓库、记录或命令输出。

## 2. 只读盘点与备份

确认 `uname -m`、`lsb_release -ds`、`python3 --version`、ROS 发行版、磁盘和各 setup.bash。Noetic 常用 Python 3.8，助手必须兼容。使用 `ip -br addr`、`ip route`、`lsusb -t`、`ps -eo pid,ppid,pgid,sid,args`、`ss -lntup` 记录网络、设备、进程和端口。仅在已有 master 时查询节点、话题、服务类型及任务/建图状态；不为盘点启动算法。

GO2_3 已验证 eth0/LAN 192.168.50.112、go2dds/DDS 192.168.123.18、eth1/雷达网口 192.168.1.50、MID360 192.168.1.119。新机器逐项实测；LAN 网卡不能代替 DDS 网卡。记录旧相机 launch 和 ROS master 的 PID/父进程/启动命令/工作目录、systemd 状态和持有者。

写入前建立 `CCS工作空间/backups/<设备ID>-<UTC时间>/`，保存将替换的文件、权限、相对路径和 SHA-256，以及启动命令、节点/PID、相关日志、地图状态和急停记录。注明原来不存在的文件。系统授时配置和首次写入 underlay 的适配 launch 也单独备份。

确认没有执行中任务、控制过渡、建图、地图生成或定位切换，再正常停止已识别的旧入口。相机原驱动可用但新入口打不开时先查旧驱动占用，只结束已确认的旧相机 launch。禁止 `pkill ros`、清空 `.ros` 或停止无归属 master。保留原生地图、标定和工作目录。

## 3. 准备实体源码与运行文件

### 3.1 选择包并冻结版本

发布包共九包。公共七包是 `EPGeneral_device_config`、`epgeneral_mqtav`、`EPGeneral_udp_telemetry`、`EPGeneral_video_srt`、`EPGeneral_map_stream`、`EPGeneral_relocalization`、`EPGeneral_task_control`。原生 Go2 加 `EPGeneral_go2_integration`；Ground-Air 加 `EPGeneral_ground_air_control`。每种设备构建七或八包，不混装全部专用适配包。

记录 `git rev-parse HEAD`、`git status --short` 和选定源码 SHA-256。比对修复时保留已有工作树修改。部署依据是实际文件，不只是版本号。deploy、documents、历史记录不放入 catkin src；CCS 包必须为实体目录，不依赖历史 vendor/bin 或指回旧 vendor 的链接。

### 3.2 LF、权限与传输

在独立 staging 整理文件和相对路径清单。Shell/Python 入口使用 UTF-8 无 BOM、LF，检查所有间接调用的脚本。CRLF 会把 shebang 解释成 `bash\r`，导致建图协商返回 127。只对本次 staging 文本规范化，之后重新计算哈希；不转换地图或二进制。Python 3.8 写 LF 使用 `open(path, 'w', encoding='utf-8', newline='\n')`，不能用该版本不支持的 `Path.write_text(newline=...)`。

传输后校验 SHA-256、恢复可执行位，用目标 Linux 的 `bash -n` 检查受影响 Shell。Windows 使用明确的 Git Bash 或目标 Linux；不可把未安装发行版的 WSL 当成验证通过。Python、YAML、launch 分别做语法和加载器检查。

### 3.3 GO2_3 首次安装示例

假定 unitree 用户已收到核验过的 `/tmp/ccs-stage`（src 下的八包与完整 deploy/go2_robot3），并完成备份与空闲确认。更换设备要先改独立 profile、脚本、预检和 launch，再调整下列路径。

~~~bash
WORKSPACE=/home/unitree/ccs_edge_ws
STAGE=/tmp/ccs-stage
STAGE_SRC="$STAGE/src"
PROFILE=go2_robot3
PROFILE_SOURCE="$STAGE/deploy/$PROFILE"
install -d -m 0750 "$WORKSPACE/src" "$WORKSPACE/scripts" \
  "$WORKSPACE/config/$PROFILE" "$WORKSPACE/docs/$PROFILE" \
  "$WORKSPACE/run/state" "$WORKSPACE/run/map/current" "$WORKSPACE/run/map/export" \
  "$WORKSPACE/maps/download" "$WORKSPACE/maps/sessions" "$WORKSPACE/maps/archive" \
  "$WORKSPACE/mission"
for pkg in EPGeneral_device_config epgeneral_mqtav EPGeneral_udp_telemetry \
  EPGeneral_video_srt EPGeneral_map_stream EPGeneral_relocalization \
  EPGeneral_task_control EPGeneral_go2_integration; do
  test -d "$STAGE_SRC/$pkg" && test ! -L "$STAGE_SRC/$pkg" || exit 1
  test ! -e "$WORKSPACE/src/$pkg" || { echo "Back up and reconcile existing $pkg first"; exit 1; }
  cp -a "$STAGE_SRC/$pkg" "$WORKSPACE/src/$pkg" || exit 1
done
install -m 0640 "$PROFILE_SOURCE/config/"*.yaml "$WORKSPACE/config/$PROFILE/"
install -m 0750 "$PROFILE_SOURCE/start_ccs_edge_dev.sh" "$WORKSPACE/start_ccs_edge_dev.sh"
install -m 0750 "$PROFILE_SOURCE/scripts/"*.py "$WORKSPACE/scripts/"
install -m 0750 "$PROFILE_SOURCE/verify_ros_contract.py" "$WORKSPACE/scripts/"
test -r "$WORKSPACE/src/EPGeneral_map_stream/launch/mapping_prerequisites_go2_robot3.launch"
find "$WORKSPACE/src" -type f \( -path '*/scripts/*.py' -o -path '*/scripts/*.sh' \) -exec chmod 0755 {} +
~~~

prerequisites launch 已随 map_stream 包提供，不可漏装。外参指向本机 `/home/unitree/go2_nav_ws/src/go2_core/config/extrinsics.yaml`。根脚本依赖 ccs_go2_preflight.py、ccs_ros_readiness.py 和 ccs_sntp_sync.py，仅复制根脚本和 YAML 不能启动。

其他设备：Scout 两个适配 launch、Wheeltec/legacy Go2 bringup 装入工作空间 launch/。Ground-Air 的 launch/、overrides/car_bringup、用户 service 和两个首次写入 car_bringup 的 launch 按[手册首次安装](USER_MANUAL.md#ground-air-首次安装补充)操作，保持 service disabled。Robot2 须把 profile/scripts/ccs_sntp_sync.py 和 ccs_ros_readiness.py 一起安装到工作空间/scripts 并赋予执行权限；2026-09-11 已同步相机独立就绪、独立进程会话及任务急停恢复代码，帧率仍为 30，授时和日志保持该 profile 原有策略。

### 3.4 依赖与构建

先 source Noetic，再加载原生 underlay，最后加载已有 CCS overlay（--extend）；全新工作空间尚无 CCS setup，不能提前 source。source ROS 前不要启用 set -u。按[手册依赖](USER_MANUAL.md#22-在设备准备-ros-依赖)补实际缺项，尤其系统 Python 的 yaml、msgpack、paho-mqtt。MAVROS 消息/构建依赖不表示每种底盘都要启动飞控节点。

~~~bash
source /opt/ros/noetic/setup.bash
source /home/unitree/go2_nav_ws/devel/setup.bash --extend
cd /home/unitree/ccs_edge_ws
rosdep install --from-paths src --ignore-src -r -y
catkin_make -j2 -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash --extend
rospack find epgeneral_go2_integration
rospack find epgeneral_task_control
rosmsg show epgeneral_task_control/TaskExecutionCommand
~~~

ARM 控制并行度。核对所有 CCS 包 rospack find/readlink -f 都在本工作空间实体 src；外部依赖仍在 underlay。Python/配置/文档增量无须 catkin 重建；改消息、C++ 或构建清单时执行相应构建。

## 4. 填写七份 config

按[接口清单](CONFIG_TOPIC_REFERENCE.md)填 topic、ROS 类型、字段、方向和启用阶段，再用[参数表](INTERFACE_REFERENCE.md)校验。实际运行配置通常在工作空间/config/profile，不是包内示例；配置无热重载。

| 文件 | 必须核对 |
| --- | --- |
| device.yaml | 唯一 ID、端侧 IP；平台同 ID 新记录，初始地图绑定为空 |
| epgeneral_mqtav.yaml | Broker、状态/连接/电池/任务来源；QRD_003 周期 low_state 判断连接，锁存 Bool.data 表示 armed |
| udp_telemetry.yaml | 正式 name/display_name/type/level 与平台严格哈希一致；source 真实类型/字段；pgm_file 是文件接口 |
| video.yaml | 驱动实际 topic/type、分辨率/帧率、SRT 端口/码率；enabled 元数据不控制节点启停 |
| map_stream.yaml | 原始输入与预览区分；backend、标定、frame/coordinates、保存服务、PCD→PGM 路径、磁盘 |
| relocalization.yaml | stages、地图文件名/下载根目录、initialpose/map/健康话题、TF 和持久状态 |
| task_control.yaml | command/feedback/status、共享地图状态、action/odom/停车、reset/enable 类型、超时及急停文件 |

GO2_3 遥测为 `/go2/imu`、`/odom_nav`、`/go2/battery_state`，算法仍用 `/livox/imu`。算法位姿可能仅按需运行时存在，不为了空闲告警重复启动 FAST-LIO。当前原生 Go2 `ros.frames.map=lio_odom`、`ros.frames.preview=odom`、`artifacts.frame=odom`；平台外参按本设备 ID 配置，不能颠倒帧或只改 header 而不转换坐标。

原生 Go2 重定位拥有导航，任务适配器 attach，共享互斥锁。导航 reset 是 `std_srvs/Trigger`，必须检查 response.success；enable/disable 是 `std_srvs/SetBool`。不能用 Empty 代替 Trigger，也不能把服务应答等同于状态已改变。重启、新任务不清急停。

## 5. 网络、授时与只读预检

确认 MQTT TCP1883、遥测 UDP14560、建图 UDP14561/14562 + HTTP TCP14600、任务 UDP14563/14564、定位 UDP14565/14566、SRT UDP9000、NTP UDP123 及防火墙方向，以实际配置为准。平台重启保留原工作目录/数据目录，避免另一实例读取空设备表。

沿用现有授时服务。timesyncd 方案备份后将 profile 的 timesyncd-ccs.conf 安装到 `/etc/systemd/timesyncd.conf.d/ccs.conf`，确认服务工作，不并行另启竞争的守护程序。**GO2_3 启动只检查平台 SNTP 有效应答，不检测时差，也不设置时钟；任务计划 UTC 容差仍为 2 秒**。其他 profile 按各自脚本策略核对。

执行[配置加载器检查](USER_MANUAL.md#33-启动前校验)、gst-inspect-1.0 srtsink/x264enc 和 bash -n。GO2_3 执行 `./start_ccs_edge_dev.sh --check`，比较前后节点/PID 和 latest：不得启停节点、创建本次日志目录、更新 latest。无 master 时确认始终不存在，不为诊断补建 master。按需服务离线只核对类型类、MD5 和 launch 静态解析，不为了检查启动导航。

~~~text
[OK] GO2 configuration, time, persistent and on-demand launch files passed preflight; no nodes were started.
~~~

文案 time 表示当前可用性探测。预检助手中的旧错误文字 “two-second startup time check” 不意味着恢复了时差门控；根脚本 --availability-only 和任务配置各有作用。

## 6. 首次启动与相机切换

旧入口停止、底盘持续 disabled 后，从工作空间运行 `./start_ccs_edge_dev.sh`。默认手动启动，不增加自启。GO2_3 依次启动雷达、默认停用的底盘桥接、D435i、MQTT/UDP/SRT、建图/定位协调器、任务协调器及适配器。底盘和输入门控通过后才消费任务，算法按需启动。

D435i 默认单设备自动选择，不传 device_type。多相机时可 `CCS_D435_SERIAL=339222070647 ./start_ccs_edge_dev.sh`（该号是 QRD_003 历史值，现场先枚举），序列号原样传递，禁止添加 `_`。RGB 640×480@15，关闭深度、红外、gyro、accel、TF。注册后最多等30秒，两帧以上、时间戳递增、年龄≤3秒才输出 `[OK] camera is ready.`，随后 SRT 启动。注册不等于图像可用。

组件统一 `[OK] <name> is ready.`；成功底层检查静默，失败回放诊断并使用 `[ERROR]`，路径指向本次 camera.log 等文件。历史相机未连接时跳过验收不代表当前入口支持绕过相机。缺硬件明确标注未完成项，不能假报最终 ready。USB2.1 和既有 Right MIPI 告警保留，通过 RGB/SRT 实测判断，不宣称硬件已修复。

~~~text
[OK] GO2 CCS services are running. Mapping/localization remain task-managed. Ctrl+C stops only this workflow.
~~~

## 7. 任务、地图生成与退出约束

下发成功仅指轨迹提交，不是导航 ready 或执行成功。已知急停在协商、prepare、commit、适配器最终检查均拒绝；传输中新增或损坏标记同样拒绝。EMERGENCY_STOP_LATCHED 不再每5秒自动准备；定位暂不可用等可恢复错误仍可重试。修复根因后按[人工复位](USER_MANUAL.md#go2-人工急停复位)调用服务，不直接删除标记。复位不使能，不自动执行旧任务，恢复需重新下发。

原生 Go2 保存时先由 `/go2_map_accumulator/save_map` 保存本次新鲜非空的 accumulator PCD，从工作空间 `run/map/current/public_map.pcd` 校验并取得本次 session 快照，再由转换脚本把该快照原子复制到 `run/map/export/public_map.pcd`，然后生成 PGM/YAML。首次没有 export PCD 正常，不以旧文件或空文件冒充成功。失败检查 save 响应、PCD 时间/点数和转换日志。成果/日志目录创建失败返回 ARTIFACT_STORAGE_UNAVAILABLE 并释放会话，修复权限或普通文件占位后重新 prepare。

GO2_3 使用工作空间锁、独立 setsid 启动自身 roscore/roslaunch 并验证 PPID。Ctrl+C/TERM 依次停止任务消费/适配器、确认底盘停用、逆序停止组件、最后停止自建 master；不停止复用 master。130/143 为信号退出码，结合清理日志判断。整终端进程组同时结束曾导致 master 先退、disable RPC 超时并持久锁存，不能恢复该启动方式。

内部 shutdown-unload 可早于本进程 ROS 关闭信号；它与 close 使用同一个幂等收尾入口，覆盖 is_shutdown_requested() 回调阶段。仅收尾上下文允许复用新鲜 disabled，且必须同时排除未完成的控制过渡及 RPC；普通 STOP/UNLOAD 仍需服务响应和状态确认，真实失败继续锁存。一次收尾复用首次结果，不把失败改成成功。当前源码还区分可恢复卸载与永久 close：收尾结束后的新 PREPARE 重新校验安全/地图/定位后才可恢复，永久关闭仍拒绝新工作；该后续修订尚未部署。完整约束见[关闭经验](GO2_DEPLOYMENT_LESSONS.md#3-幂等关闭不能变成绕过停用或永久禁止复用)。出现 `/go2_sdk_bridge_real exited` 先区分进程真实退出与 rosnode 查询失败；现监控重试 master 查询，不能屏蔽真实故障。

## 8. 验收与回滚

首次做完整静态/依赖/包解析/协议检查，再在授权范围验证真机。修复仅跑受影响增量，不自动扩大为全量或重建。记录命令、通过数、未测项及原因；模拟通过不能写成实机通过。

| 阶段 | 记录内容 |
| --- | --- |
| 静态/接口 | 七 YAML、launch、LF/BOM/权限、实体包路径、ROS类型/MD5、硬件/网络、--check 无副作用 |
| 首次通信 | ≥60秒实际计数/帧率，平台正式解析并存储三级 UDP、心跳/MQTT、电池/IMU/雷达；sendto 不能替代平台证据 |
| 相机增量 | 按实际 profile 检查分辨率和帧率；Robot3 为30帧窗口640×480、约13–17Hz，Robot2 为640×480、约30Hz。新鲜帧先于 camera ready，SRT 节点存活且最终 ready；平台解码按本轮范围另验 |
| 急停/日志增量 | 跨≥3个原重试周期不刷错、锁存拒绝/人工复位约束、日志归档、有序退出再启动保持 disabled |
| 联合下发增量 | 在实际 transfer_seconds 内连续 prepare/chunk/commit；匹配新请求 ID、ACK/缺片、本轮 XML 落盘及原版本/CRC/航点；超时后的幂等 ACK 不作完整传输证据 |
| 真实任务 | 未授权使能/运动时保持 disabled，不发送目标、不做整轮建图/定位/导航；现场后续确认按来源另行追加，保留此前工具验收范围 |

失败先停止本次拥有的流程，保存日志及最新急停状态；按清单恢复匹配备份、权限、代码配置，校验原 SHA-256。禁止用备份覆盖更新的安全状态。恢复旧相机前确认新相机已退出，再用原命令/目录启动。归属或停用状态不明时保留诊断，不强行拉起旧整栈。

## 9. 日志与交付

GO2_3 正常启动在 `/home/unitree/.ros/ccs_edge_ws/<UTC启动时间_纳秒_PID>/` 建目录，latest 指向最近取得工作空间锁的启动。包含 startup.log、组件输出、runtime_monitor.log、ROS_LOG_DIR 的 ros/、mqtav/、relocalization/、mapping/。建图事件在 mapping/map_stream.log，FAST-LIO/PGM 在 `mapping/sessions/<session>/`。CCS_EDGE_LOG_ROOT 可改根目录；--check 不改变日志。其他 profile 保留原路径，见手册。

可选 launch log_dir 只改变建图日志，未传保留旧行为，仍校验会话路径边界。地图成果、任务、锁和急停继续在配置的工作空间路径，不随 latest 移动。

交付追加到 `deploy/records/<设备ID>/DEPLOYMENT.md`：时间/范围、commit与修改摘要、文件清单、前后哈希、备份/证据路径、命令、实测、告警/跳过项、启停和回滚。不覆盖历史日期或失败记录，不包含密码。

端侧文档保存一个完整的 edge 发布包解压树，例如 <工作空间>/docs/distribution/，保留 edge_side_pkg/ 与 docs/EDGE_DEVICE_INTERFACES.md 的相对层级。这样配置、脚本、接口和历史记录的链接均可离线打开；它只用于追溯，运行包仍解析到实体 src，禁止把这个文档归档重新当作 vendor underlay。

`<工作空间>/docs/<profile>/DEPLOYMENT.md` 改为短入口，链接到 ../distribution/edge_side_pkg/deploy/records/<设备ID>/DEPLOYMENT.md；完整记录只维护该一份。同步新记录时更新归档树中的对应文件，保留历史章节，并同时更新本次指南/接口/手册。若部署来自源码而非 edge ZIP，按同一层级整理并检查全部本地链接，不能只复制含相对链接的单个文档。清单记录文档及运行增量的实际哈希。
