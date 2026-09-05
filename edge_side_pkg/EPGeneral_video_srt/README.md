# epgeneral_video_srt

配套 CCS 0.23.1：[完整使用手册](../documents/USER_MANUAL.md) · [设备内接口与参数](../documents/INTERFACE_REFERENCE.md)。包级 launch 默认读取共享配置包；一键脚本显式读取工作空间 `config/<profile>`，修改后需重启。

当前版本：`v0.1.1`

运行配置统一由 `epgeneral_device_config/config/video.yaml` 提供；设备专用视频参数保存在对应部署 profile。

该 ROS Noetic 包订阅现有 `sensor_msgs/Image` 或 `sensor_msgs/CompressedImage`
话题，将图像编码为低延迟 baseline H.264，封装为 MPEG-TS，并通过 SRT Listener
发送。节点不启动摄像头驱动。

默认监听地址为 `srt://:9000?mode=listener&transtype=live&latency=120000`（绑定配置中的 `0.0.0.0`）；如果配置具体本地地址，节点会将其用于 Listener。YAML 延迟单位为毫秒，生成 SRT URI 时转换为微秒。地面站使用设备 IP 作为
SRT Caller 连接，端侧无需配置地面站地址。

## 环境与安装

- Ubuntu 20.04、ROS Noetic、GStreamer 1.16+
- `cv_bridge`、`image_transport`、GStreamer app/base/good/bad/ugly 插件

```bash
sudo apt update
sudo apt install ros-noetic-cv-bridge ros-noetic-image-transport libopencv-dev \
  libgstreamer1.0-dev gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
gst-inspect-1.0 srtsink
```

`gst-inspect-1.0 srtsink` 必须成功。防火墙需要放行端侧 UDP 9000。

如果 roslaunch 显示 `process ... died ... exit code 1`，请先查看同目录的节点日志。该包会在启动时明确报告缺失的 GStreamer 元素；其中 `srtsink` 缺失时安装 `gstreamer1.0-plugins-bad`，并检查 `GST_PLUGIN_PATH` 没有覆盖系统插件目录：

```bash
gst-inspect-1.0 srtsink
echo "$GST_PLUGIN_PATH"
```

节点日志中应出现完整管线、`SRT listener bound` 和 `waiting for a ground-station caller`。若管线创建成功但无法连接，检查 UDP 端口占用和防火墙：

```bash
ss -lunp | grep ':9000'
sudo ufw allow 9000/udp
```

## 配置与启动

`epgeneral_device_config/config/video.yaml` 的主要参数：

```yaml
image_topic: "/camera/image_raw"
image_message_type: "sensor_msgs/Image"
output_width: 640
output_height: 480
framerate: 30
bitrate_kbps: 2000
srt_bind_address: "0.0.0.0"
srt_port: 9000
srt_latency_ms: 120
frame_timeout_seconds: 5.0
```

```bash
cd ~/catkin_ws
catkin_make
source devel/setup.bash
roslaunch epgeneral_video_srt epgeneral_video_srt.launch
```

压缩图像将 `image_message_type` 设置为 `sensor_msgs/CompressedImage` 并填写对应压缩话题。RealSense D435i 参数保存在 `deploy/go2_edu/config/video.yaml`；适配 launch 需显式传入集中配置：

```bash
roslaunch epgeneral_video_srt epgeneral_realsense_d435i_srt.launch \
  video_config_file:="$(rospack find epgeneral_device_config)/config/video.yaml"
```

本包只订阅话题，摄像头驱动需单独启动。可用下列命令检查输入和本机输出：

```bash
rostopic type /camera/image_raw
rostopic hz /camera/image_raw
ffplay "srt://127.0.0.1:9000?mode=caller&transtype=live&latency=120000"
```

编码链为 `appsrc -> videoconvert -> x264enc -> h264parse -> mpegtsmux -> srtsink`；通配地址使用 `srt://:<port>?mode=listener`，避免部分 SRT 插件将 `0.0.0.0` 当成远端地址解析失败。
MPEG-TS 使用 7 个 188 字节包对齐，即每次 1316 字节；H.264 禁用 B 帧并周期插入
SPS/PPS。SRT 面向可信局域网，本版本不提供加密、认证、音频、录像或多客户端分发。
