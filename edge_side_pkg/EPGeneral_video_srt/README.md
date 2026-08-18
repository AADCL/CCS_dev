# epgeneral_video_srt

当前版本：`v0.1.0`

该 ROS Noetic 包订阅现有 `sensor_msgs/Image` 或 `sensor_msgs/CompressedImage`
话题，将图像编码为低延迟 baseline H.264，封装为 MPEG-TS，并通过 SRT Listener
发送。节点不启动摄像头驱动。

默认监听地址为 `srt://0.0.0.0:9000?mode=listener`。地面站使用设备 IP 作为
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

## 配置与启动

`config/video.yaml` 的主要参数：

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

压缩图像可使用 `config/compressed_video.yaml`。RealSense profile 使用：

```bash
roslaunch epgeneral_video_srt epgeneral_realsense_d435i_srt.launch
```

本包只订阅话题，摄像头驱动需单独启动。可用下列命令检查输入和本机输出：

```bash
rostopic type /camera/image_raw
rostopic hz /camera/image_raw
ffplay "srt://127.0.0.1:9000?mode=caller&transtype=live&latency=120000"
```

编码链为 `appsrc -> videoconvert -> x264enc -> h264parse -> mpegtsmux -> srtsink`。
MPEG-TS 使用 7 个 188 字节包对齐，即每次 1316 字节；H.264 禁用 B 帧并周期插入
SPS/PPS。SRT 面向可信局域网，本版本不提供加密、认证、音频、录像或多客户端分发。
