# 端侧功能包使用手册

配套 CCS 0.23.1，更新日期：2026-09-05。本手册面向部署和现场使用人员。参数定义见[接口参考](INTERFACE_REFERENCE.md)，功能包和版本见[端侧 README](../README.md)。

## 1. 选择设备与部署形式

端侧 ZIP 是源码与部署资料集合，不是可在设备直接双击运行的安装器。Windows/Ubuntu 安装包用于地面站，不能代替 ROS 端侧构建。源码仓库和 edge ZIP 的操作都从其 edge_side_pkg 目录开始。

| profile | 示例 SSH 目标 | CCS 工作空间 | 操作入口 |
| --- | --- | --- | --- |
| go2_edu | nvidia@192.168.50.100 | /home/nvidia/ccs_edge_ws | 前台一键脚本 |
| scout_mini | nvidia@192.168.50.120 | /home/nvidia/ccs_edge_ws | 前台一键脚本 |
| wheeltec_r550p | nrc19@192.168.50.122 | /home/nrc19/ccs_edge_ws | 前台一键脚本 |
| ground_air_agv | bitcq@192.168.50.130 | /home/bitcq/ccs_edge_ws | 手动启动用户 systemd 服务 |

这些是仓库 profile 示例，现场 ID/IP、用户和路径不同时先修改配置，不能仅改 SSH 目标。Go2 无可用重定位后端且脚本不启动任务；Ground-Air 脚本不启动任务；Wheeltec 无视频输入。Scout/Wheeltec 任务需要外部导航栈。

## 2. 准备源码、配置与依赖

### 2.1 在指控端准备 staging

以下为 Bash 示例；先设置实际源码或已解压 edge ZIP 中的绝对路径。staging 是临时副本，不覆盖仓库 profile 原件。

~~~bash
EDGE_SRC=/path/to/CCS_dev/edge_side_pkg
PROFILE=scout_mini
STAGING=$(mktemp -d)
mkdir -p "$STAGING/src"
for package in EPGeneral_device_config epgeneral_mqtav EPGeneral_udp_telemetry \
  EPGeneral_video_srt EPGeneral_map_stream EPGeneral_relocalization EPGeneral_task_control
do
  cp -a "$EDGE_SRC/$package" "$STAGING/src/"
done
if [ "$PROFILE" = ground_air_agv ]; then
  cp -a "$EDGE_SRC/EPGeneral_ground_air_control" "$STAGING/src/"
fi
cp "$EDGE_SRC/deploy/$PROFILE/config/"*.yaml \
  "$STAGING/src/EPGeneral_device_config/config/"
~~~

发布归档有八包，普通设备只选择七包。Ground-Air 第八包需要外部 ground_air_msgs，不能为了“全选”将它强行加入其他设备构建。deploy、documents 不进入 catkin src；设备脚本、launch 和系统配置按用途另行安装。

### 2.2 在设备准备 ROS 依赖

使用 Ubuntu 20.04、ROS Noetic、系统 Python 3。以下为公共依赖补充，外部底盘、相机、Livox、算法和导航包按设备专项指南准备。

~~~bash
sudo apt update
sudo apt install python3-yaml python3-paho-mqtt python3-msgpack python3-numpy \
  python3-catkin-pkg python3-rospkg ros-noetic-mavros ros-noetic-mavros-extras \
  ros-noetic-cv-bridge ros-noetic-image-transport ros-noetic-sensor-msgs \
  ros-noetic-nav-msgs ros-noetic-geometry-msgs ros-noetic-diagnostic-msgs \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
~~~

先按[工作空间接口](INTERFACE_REFERENCE.md)加载设备 underlay，再加载 CCS。Scout 顺序为 Noetic → RealSense → Scout navigation → livox_fastlio → CCS；Ground-Air 为 Noetic → 车辆 catkin_ws → CCS。不得将地面站的 Python 虚拟环境带入 ROS Noetic 构建。

### 2.3 安装和构建

通过 SSH/SCP/rsync 或其他现场文件传输方式，将 staging 的 src 下所选包放到设备 CCS 工作空间 src 中。已有同名包应先备份整个旧包，以免增量覆盖遗留已删除文件；不要覆盖外部算法工作空间。

设备上在正确 underlay 环境中执行，WORKSPACE 按所选设备设置：

~~~bash
WORKSPACE=/home/nvidia/ccs_edge_ws
mkdir -p "$WORKSPACE/src"
cd "$WORKSPACE"
rosdep install --from-paths src --ignore-src -r -y
catkin_make --force-cmake -DPYTHON_EXECUTABLE=/usr/bin/python3
source devel/setup.bash
rospack find epgeneral_device_config
rospack find epgeneral_task_control
rosmsg show epgeneral_task_control/TaskExecutionCommand
~~~

构建失败先检查缺少的 ROS 包是否来自 underlay，以及 rospack 的解析路径。Ground-Air 用 `rospack find ground_air_msgs` 检查消息包。源码中 Python 节点入口由 catkin 安装，脚本权限应按部署指南设置；不要混用 Python 2。

## 3. 安装实际运行配置

### 3.1 两种入口

| 使用方式 | 修改位置 | 启动方式 |
| --- | --- | --- |
| 单包 launch 默认值 | epgeneral_device_config/config | 不传配置路径时读取包内 YAML |
| 一键脚本 | 工作空间/config/profile | 脚本显式传入这些文件 |
| 单包调试指定文件 | 任意已核验的配置目录 | 显式传入两个配置路径，推荐沿用设备运行目录 |

若改了包内 YAML，但脚本仍读 config/profile，运行行为不会变化。Go2 还会优先尝试脚本旁 config/device.yaml；显式设置 CCS_EDGE_PROFILE_CONFIG_DIR 可消除歧义。

将所选 profile 的七份 YAML 传到设备运行目录。以已在设备临时目录取得 profile 文件为例：

~~~bash
PROFILE=scout_mini
WORKSPACE=/home/nvidia/ccs_edge_ws
PROFILE_SOURCE=/tmp/ccs-profile
install -d -m 0750 "$WORKSPACE/config/$PROFILE"
install -m 0640 "$PROFILE_SOURCE/config/"*.yaml "$WORKSPACE/config/$PROFILE/"
install -m 0750 "$PROFILE_SOURCE/start_ccs_edge_dev.sh" "$WORKSPACE/start_ccs_edge_dev.sh"
~~~

设备适配 launch 的安装位置以[专项指南](#9-设备专项操作)为准。尤其 Scout 使用额外 base launch，Ground-Air 还需 CCS launch、局部 overrides 和用户服务，不能只复制 YAML 就启动。

### 3.2 修改必检项

- device.yaml：设备唯一 ID 与自身 IP，和地面站设备表一致。
- epgeneral_mqtav.yaml：Broker 地址；UDP/map_stream/relocalization/task_control 的 network：上行目标与端口；不要将设备自身 IP 填成地面站 IP。
- 各包的 ROS topic/message_type/字段路径/frame 与真实外部包对应；descriptor 身份变更需同步地面站描述符。
- 建图外参必须来自设备标定，不能直接复制另一底盘的变换。
- 重定位 map_root/state_file、任务适配器及 UDP pgm_file 的对应路径保持一致。
- timesyncd-ccs.conf 与 CCS_NTP_SERVER 对齐。修改地面站地址时逐份检查 YAML 和脚本，不能仅设置一个环境变量。

修改前停止对应节点并备份实际运行配置：

~~~bash
CFG="$WORKSPACE/config/$PROFILE"
cp -a "$CFG" "$CFG.backup-$(date +%Y%m%d-%H%M%S)"
editor "$CFG/device.yaml"
editor "$CFG/epgeneral_mqtav.yaml"
~~~

将 editor 换成现场可用编辑器。YAML 使用空格缩进、真正的 bool/数字，字段约束逐项查接口参考。修改节点路径或配置后无需重新编译 Python 包，但修改 .msg/C++/catkin 清单后必须重新构建。

### 3.3 启动前校验

在 source CCS 后，使用真实加载器检查六类 Python 配置；此步骤不启动 ROS 节点或外部算法：

~~~bash
export CCS_CHECK_CONFIG="$CFG"
python3 - <<'PY'
import os
from pathlib import Path
from epgeneral_mqtav.config import load_config as mqtt
from epgeneral_udp_telemetry.config import load_config as telemetry
from epgeneral_map_stream.config import load_config as mapping
from epgeneral_relocalization.config import load_config as relocalization
from epgeneral_task_control.config import load_config as task
root = Path(os.environ["CCS_CHECK_CONFIG"])
for filename, loader in [
    ("epgeneral_mqtav.yaml", mqtt), ("udp_telemetry.yaml", telemetry),
    ("map_stream.yaml", mapping), ("relocalization.yaml", relocalization),
    ("task_control.yaml", task),
]:
    loader(str(root / filename), str(root / "device.yaml"))
    print("OK", filename)
PY
~~~

建图专有参数还在 prepare 的外部命令预检中验证。视频参数由 C++ 节点读取和检查，先执行 `gst-inspect-1.0 srtsink`、`gst-inspect-1.0 x264enc`，再按单包步骤启动验证。解析通过仅代表配置结构可读，不代表外部话题、地图、服务和 TF 已就绪。

## 4. 授时、网络及整机启停

### 4.1 授时与端口

按现场授时服务管理方式安装 profile 的 timesyncd 配置；使用本仓库 timesyncd 方案时：

~~~bash
sudo install -D -m 0644 "$PROFILE_SOURCE/config/timesyncd-ccs.conf" \
  /etc/systemd/timesyncd.conf.d/ccs.conf
sudo systemctl restart systemd-timesyncd
timedatectl timesync-status
timedatectl show -p NTPSynchronized
~~~

服务器应为地面站地址，同步应为 yes。一键脚本授时预检失败会阻止新节点启动。

| 接收端 | 端口 |
| --- | --- |
| 地面站 | TCP 1883 MQTT；UDP 123 NTP；UDP 14560 遥测、14562 建图、14564 任务、14566 重定位 |
| 设备 | UDP 9000 SRT、14561 建图控制、14563 任务、14565 重定位；TCP 14600 建图下载 |
| ROS 通信 | ROS Master 及节点动态 TCPROS 端口，按部署网络放行 |
| 重定位地图下载 | 端侧访问地面站下发的 HTTP URL，地址和端口以地面站配置为准 |

通道运行于可信局域网；开放端口不等于部署认证或加密。不要将 UDP 控制和地图文件服务直接暴露到公网。

### 4.2 Go2、Scout、Wheeltec

~~~bash
export CCS_EDGE_WORKSPACE="$WORKSPACE"
export CCS_EDGE_PROFILE_CONFIG_DIR="$WORKSPACE/config/$PROFILE"
"$WORKSPACE/start_ccs_edge_dev.sh"
~~~

保持前台终端；按 Ctrl+C 停止脚本管理的进程及由其自身创建的 ROS Master。已有 ROS Master 不应被当成本次新建进程清理。Wheeltec 停止过程还发送零速度。Scout 对硬件话题等待实际消息，不能以 rostopic list 出现名称代替数据就绪。

Go2 日志通常在 ~/.ros/ccs_edge_dev/log；Scout 和 Wheeltec 分别在 ~/.ros/ccs_edge_dev_scout_mini/log、~/.ros/ccs_edge_dev_wheeltec_r550p/log，可由 CCS_EDGE_STATE_DIR 改变。

### 4.3 Ground-Air

完成专项部署后，以 bitcq 用户手动操作：

~~~bash
systemctl --user start ccs-edge-dev.service
systemctl --user status ccs-edge-dev.service --no-pager
journalctl --user -u ccs-edge-dev.service -n 80 --no-pager
systemctl --user stop ccs-edge-dev.service
systemctl --user is-enabled ccs-edge-dev.service
~~~

最后一项预期 disabled。不要执行 enable，也不要与前台一键脚本、旧整栈 launch 并行运行。组件日志在 /home/bitcq/ccs_edge_ws/log/ground_air_agv；服务 stdout/stderr 配置写入 ~/.ros/ccs_edge_dev_ground_air_agv/log/supervisor.log，journal 主要用于服务生命周期。

## 5. 八个功能包的独立使用

以下单包命令仅在对应一键节点已停止、ROS 环境已 source、CFG 指向实际配置目录时执行。每个 roslaunch 保持前台，Ctrl+C 停止；无需同时启动所有包。

### 5.1 epgeneral_device_config

配置资源包没有节点。用 `rospack find epgeneral_device_config` 定位，检查七份 YAML。改变设备 ID 后同步地面站登记并重启 MQTT、UDP、视频、建图、重定位及任务相关节点，旧会话不能继续沿用。

### 5.2 epgeneral_mqtav

~~~bash
roslaunch epgeneral_mqtav epgeneral_mqtav.launch \
  device_config_file:="$CFG/device.yaml" config_file:="$CFG/epgeneral_mqtav.yaml"
~~~

先用 rostopic type/echo 检查配置的状态和电池源，再在地面站确认 presence、heartbeat、status。正常退出发布 offline；异常断线由 MQTT Last Will/心跳超时体现。在线不代表 UDP/视频/任务可用。没有电池源时保持 unknown，诊断网络时检查 Broker TCP 1883 与节点耐久日志。

### 5.3 epgeneral_udp_telemetry

~~~bash
roslaunch epgeneral_udp_telemetry epgeneral_udp_telemetry.launch \
  device_config_file:="$CFG/device.yaml" telemetry_config_file:="$CFG/udp_telemetry.yaml" \
  destination_host:=192.168.50.101 destination_port:=14560
rostopic echo -n 1 /epgeneral_udp_telemetry/diagnostics
~~~

把 destination_host 替换为实际地面站地址；该 launch 默认会覆盖 YAML，不传参会使用 192.168.151.100。诊断命令在另一个已 source 终端执行。profile 可能重命名诊断话题，应使用实际 launch 配置。accepted_count 应随有效源增长；descriptor hash 不匹配、未知设备、NaN/Inf、旧 session 或乱序需结合地面站日志排查。

### 5.4 epgeneral_video_srt

先启动外部相机驱动并确认 Image/CompressedImage 有帧：

~~~bash
roslaunch epgeneral_video_srt epgeneral_video_srt.launch \
  device_config_file:="$CFG/device.yaml" video_config_file:="$CFG/video.yaml"
~~~

在地面站设备详情打开视频；或从有 SRT 支持的客户端测试：

~~~bash
ffplay 'srt://192.168.50.120:9000?mode=caller&transtype=live&latency=120000'
~~~

使用实际设备 IP。视频无画面时依次检查输入类型和帧、GStreamer 插件、UDP 9000、防火墙及 Caller 参数。Wheeltec 无相机时不要仅因 YAML 的 enabled=false 就手动启动视频；该键不被节点读取。

### 5.5 epgeneral_map_stream

~~~bash
roslaunch epgeneral_map_stream epgeneral_map_stream.launch \
  device_config_file:="$CFG/device.yaml" mapping_config_file:="$CFG/map_stream.yaml"
~~~

在地面站地图工作台选择设备并准备建图，等待 prepare 成功后启动；联合建图选择主设备与参与设备，检查各设备预览 frame 一致。准备会检查输入和外部工具，但不应抢占 Ground-Air 阶段服务。

结束时用“停止并保存”完成后端保存/转换并取得成果，等待 PCD/PGM/YAML 和 manifest 可下载后再开始下一会话；abort 用于放弃当前会话，不保证生成成果。Go2 保存 accumulator，Scout/Wheeltec 执行 finalize，Ground-Air 通过 stage/save 服务，不能把 Go2 保存命令通用于其他设备。HTTP 14600 下载失败应检查设备身份地址、令牌期限、文件大小和目录剩余空间。

### 5.6 epgeneral_relocalization

~~~bash
roslaunch epgeneral_relocalization epgeneral_relocalization.launch \
  device_config_file:="$CFG/device.yaml" config_file:="$CFG/relocalization.yaml"
~~~

地面站选择地图和设备，下发地图，等待校验及定位栈启动；在地图中选择初始位姿后等待结果。核对 map/odom TF、状态 JSON 和地面站地图绑定。重复定位按地面站替换流程清理旧进程和绑定，不手工伪造 localized。Go2 当前 enabled=false，不宣称可完成定位。

Ground-Air 第一个有效 TF 即成功，之后 1 Hz 更新或缓存重发，后续持久化限频 30 秒；设备静止时重复结果是正常行为。Scout/Wheeltec 要满足稳定样本窗口。停止时先结束当前定位流程再关闭协调器，避免将残存算法节点误当作下次会话。

### 5.7 epgeneral_task_control

通用协调器用于接入自定义执行器：

~~~bash
roslaunch epgeneral_task_control epgeneral_task_control.launch \
  device_config_file:="$CFG/device.yaml" task_config_file:="$CFG/task_control.yaml"
~~~

Scout 使用 scout_task_control.launch，Wheeltec 使用 navigation_task_control.launch，参数同上；这两个入口已包含协调器，不能再重复启动通用入口。自定义执行器需实现[消息契约](INTERFACE_REFERENCE.md)。

操作顺序：完成实时重定位 → 地面站创建有效地图航点任务 → 下发并 commit → 等待准备完成/ready → 确认统一 UTC → 执行 → 查看真实反馈。任务 XML 被原子保存，任务目录和地图根目录应可写/可读。停止使用地面站任务停止命令；删除/卸载与常规停止不同，按协调器状态释放导航。适配器失联、位姿陈旧或反馈超时必须排查，不能跳过检查直接发 move_base 目标。

### 5.8 epgeneral_ground_air_control

仅用于 Ground-Air，常规启动由一键脚本启动 stage manager。单独排障时先停止一键服务，再在正确 underlay/overlay 环境中启动：

~~~bash
rosrun epgeneral_ground_air_control ground_air_stage_manager_node.py
~~~

另一个终端检查服务和 guard：

~~~bash
rosservice type /ground_air/system/set_stage
rosparam get /ground_air_stage_manager/ccs_session_guard_version
rosparam get /ground_air_stage_manager/external_tf_required
rostopic echo -n 1 /ground_air/system/stage
~~~

guard 应为 2，external_tf_required 为 1。此单节点命令不替代常驻静态 TF、驱动和外部算法；完整联调仍使用一键脚本。重定位控制 launch 的 map_id 必填，通过局部 car_bringup override 调用，通常由重定位包自动启动。不要手工争抢正在建图或定位的 stage；manager 依 caller/map_id 管理归属。

## 6. 日常验收

在已 source 的诊断终端检查：

~~~bash
rosnode list
rostopic hz /livox/imu
rostopic hz /livox/lidar
ss -lntup
timedatectl timesync-status
~~~

按实际 profile 替换话题；确认实际接收频率而非只有名称。地面站分别核对 MQTT 在线、UDP 数据更新、视频帧、地图成果和任务反馈。文件时间戳或端口监听不能替代端到端确认。

本次文档发行的自动测试不连接设备，也不代表完成 ROS 编译、真实相机或运动控制验收。现场验收应记录产品/包版本、profile、设备、日期、配置校验值及实际结果。

## 7. 故障排查

| 现象 | 优先检查 | 处理 |
| --- | --- | --- |
| 修改 YAML 无效果 | 实际 launch/脚本路径和参数覆盖 | 修改真正的 config/profile，重启对应节点 |
| rospack 找不到包 | source 顺序、构建输出、包名大小写 | 重新构建并加载正确 overlay |
| Ground-Air 同名 car_bringup 报错 | 子进程 prepend/exclude | 恢复 profile 配套配置，勿全局屏蔽 underlay |
| 整机预检停止 | NTP ServerAddress/同步、话题首条消息、驱动日志 | 先恢复依赖，不跳过检查 |
| MQTT 在线但遥测空白 | descriptor hash、diagnostics、IP/ID、字段映射 | 同步地面站定义，修复具体来源 |
| 建图 prepare 被拒绝 | 输入类型/frame/TF、外部 launch、guard、空间 | 按错误修复配置；不提前调用阶段服务绕过 |
| 地图预览漂移 | 点真实坐标、配对位姿、外参和 preview frame | 成对修改端侧与地面站帧契约 |
| 重定位一直等待 | map topic、initialpose 订阅者、TF、下载路径 | 核查外部定位栈和所选地图 |
| 任务不能 ready/执行 | localized 状态、PGM、导航 action、UTC | 先完成定位和导航准备 |
| 停止后节点仍在 | 是否属于外部 underlay 或其他会话 | 按所有权停止，不使用无差别 pkill |
| SRT 无画面 | 相机帧/类型、插件、端口、客户端支持 | 独立验证图像源与编码链路 |

## 8. 升级与回滚

升级前结束建图/任务/定位会话，停止一键栈；备份所选源码包、实际运行 YAML、脚本/launch/override、任务目录和地图状态。记录当前包版本和配置校验值，保持备份在工作空间 src 外。

安装新包和运行配置后重新构建、source、配置校验并逐模块验收。回滚时停止新栈，恢复同一批次源码、配置和适配文件，重新构建，再验证消息、TF 和地面站绑定。Ground-Air 的建图帧配置需与地面站配套回滚；不要单独恢复一端。不要把旧 localized JSON 当作回滚后的实时定位，重新完成定位再运行任务。

## 9. 设备专项操作

- [Go2 EDU](../deploy/go2_edu/DEPLOYMENT.md)：算法 underlay、相机及时间同步。
- [Scout Mini](SCOUT_MINI_DEPLOYMENT.md)：RealSense/navigation/Livox source 顺序、BMS、相机和导航适配。
- [Wheeltec R550P](WHEELTEC_R550P_DEPLOYMENT.md)：底盘、无视频部署、地图工具和停车行为。
- [Ground-Air 基础](GROUND_AIR_AGV_DEPLOYMENT.md)、[建图](GROUND_AIR_AGV_MAPPING_DEPLOYMENT.md)、[重定位](GROUND_AIR_AGV_RELOCALIZATION_DEPLOYMENT.md)：用户服务、外部 TF owner、阶段互斥及 override 安装。

专项日志保留原日期和版本，供追溯与回滚；本手册给出当前操作入口。发布安装和预发布验证范围见地面站发行说明。
