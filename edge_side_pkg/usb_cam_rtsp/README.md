# usb_cam_rtsp

当前版本：`v0.1.0`

ROS Melodic 端侧视频包。它启动 `usb_cam`，订阅 `/usb_cam/image_raw`，并通过 GStreamer RTSP Server 输出低延迟 H.264 视频：

```text
rtsp://<device.ip>:8554/usb_cam
```

## 安装

```bash
sudo apt update
sudo apt install ros-melodic-usb-cam ros-melodic-cv-bridge ros-melodic-image-transport \
  libgstreamer1.0-dev libgstrtspserver-1.0-dev \
  gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
```

将整个 `edge_side_pkg` 放入 catkin 工作空间的 `src` 后运行 `catkin_make`。设备身份修改 `edge_device_config/config/device.yaml`，相机与编码参数修改 `config/video.yaml`。

## 启动与验证

```bash
source /opt/ros/melodic/setup.bash
source ~/catkin_ws/devel/setup.bash
roslaunch usb_cam_rtsp usb_cam_rtsp.launch
gst-launch-1.0 rtspsrc location=rtsp://127.0.0.1:8554/usb_cam latency=100 \
  ! rtph264depay ! avdec_h264 ! autovideosink
```

默认使用 `/dev/video0`、640×480、30 FPS 和 2000 kbps。端侧防火墙需要允许 TCP 8554 以及 RTSP 协商使用的媒体连接。首版仅适用于可信局域网，不提供认证、TLS、录制或音频。

若日志持续显示等待相机帧，检查 `/dev/video0` 权限与 `rostopic hz /usb_cam/image_raw`。若提示缺少 `x264enc`，安装 `gstreamer1.0-plugins-ugly`。
