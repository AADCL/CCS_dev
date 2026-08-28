# WheelTech R550P 端侧部署说明

## 设备基线

- 设备：轮趣 WheelTech R550P 四轮差速底盘
- CCS ID：`UGV_003`
- SSH/ROS IP：`192.168.50.122`
- 地面站：`192.168.50.101`
- 用户及主目录：`nrc19`、`/home/nrc19`
- CCS 工作空间：`/home/nrc19/ccs_edge_ws`
- 外部设备栈：`/home/nrc19/livox_fastlio`
- 系统：Ubuntu 20.04.6、ROS Noetic、Jetson NX aarch64

## 部署结构

`ccs_edge_ws` 只保存七个 CCS 通用包、`config/wheeltec_r550p` 和根目录启动脚本。WheelTech 底盘、Livox、FAST-LIO、建图、NDT 和 TEB 包继续由 `livox_fastlio` 提供，部署过程不得修改该外部工作空间。

端侧状态、PID 和日志使用 `~/.ros/ccs_edge_dev_wheeltec_r550p`。地图继续使用 `~/livox_fastlio/maps`，下载地图使用 `~/livox_fastlio/maps/ccs_download`。

## 设备接口

| 功能 | ROS 接口 |
|---|---|
| 底盘在线状态 | `/odom` (`nav_msgs/Odometry`) |
| 底盘电压 | `/PowerVoltage` (`std_msgs/Float32`) |
| Livox 点云/IMU | `/livox/lidar`、`/livox/imu` |
| FAST-LIO 位姿 | `/fastlio_odom` |
| FAST-LIO 原始里程计 | `/Odometry` |
| 控制 | `/cmd_vel`，仅 `linear.x` 和 `angular.z` |
| 地图/里程计坐标 | `map`、`odom`、`base_link`、`body` |

电池化学体系和放电曲线未知，端侧只上报电压，地面站不估算百分比。设备未安装摄像头，视频包默认禁用。

## 部署方法

1. 通过 SSH 登录并确认 `/dev/wheeltec_controller`、`wlan0=192.168.50.122`、`eth0=192.168.1.5` 和 Mid-360 `192.168.1.165`。
2. 安装 `python3-msgpack`、`python3-paho-mqtt`，并对七个 CCS 包执行 `rosdep install`。
3. 创建 `/home/nrc19/ccs_edge_ws/src`，复制七个适用 CCS 包；安装 profile 和根目录脚本。
4. 按 ROS Noetic、`livox_fastlio`、`ccs_edge_ws` 顺序加载环境，以 `catkin_make -j1` 编译。
5. 安装 timesyncd profile，使 NTP 服务端固定为 `192.168.50.101`。
6. 执行 `./start_ccs_edge_dev.sh`，检查基础话题、CCS 节点、MQTT、UDP 和监听端口。
7. 按 `Ctrl+C` 验证零速度发布、进程清理和日志落盘。

## 安全边界

- 本次验收不发送非零 `/cmd_vel`，不发送导航目标。
- `factory_a` 只用于加载地图及节点/TF 链检查，不据此宣称全局定位成功。
- 建图测试只允许 prepare/start/abort，不生成或替换正式地图。
- 安装摄像头并确认图像话题前，不启用视频 launch。

实际执行结果、时间、命令和未验证项见 `WHEELTEC_R550P_DEPLOYMENT_LOG.md`。
