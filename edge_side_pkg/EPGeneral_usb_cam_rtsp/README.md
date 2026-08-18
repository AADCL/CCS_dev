# epgeneral_usb_cam_rtsp

当前版本：`v0.2.0`

ROS 图像话题到本机 RTSP 的低延迟 H.264 推流包。节点不启动摄像头驱动，只订阅配置指定的现有 ROS 视频话题，因此可连接 USB 相机、RealSense、网络相机桥接或其他图像发布节点。

默认地址：

```text
rtsp://<device.ip>:8554/usb_cam
```

## 配置

`config/video.yaml`：

```yaml
image_topic: "/camera/image_raw"
image_message_type: "sensor_msgs/Image"  # 或 sensor_msgs/CompressedImage
output_width: 640
output_height: 480
framerate: 30
rtsp_bind_address: "0.0.0.0"
rtsp_port: 8554
rtsp_mount_point: "/usb_cam"
bitrate_kbps: 2000
frame_timeout_seconds: 5.0
```

原始图像由 `cv_bridge` 转为 BGR8；压缩图像使用 OpenCV 解码。输入会缩放到配置的输出分辨率。为兼容旧配置，未设置 `output_width/output_height` 时仍读取 `image_width/image_height`。

## 安装与启动

```bash
sudo apt update
sudo apt install ros-noetic-cv-bridge ros-noetic-image-transport libopencv-dev \
  libgstreamer1.0-dev libgstrtspserver-1.0-dev gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
cd ~/catkin_ws
catkin_make
source devel/setup.bash
roslaunch epgeneral_usb_cam_rtsp epgeneral_usb_cam_rtsp.launch
```

摄像头驱动应由独立 launch 启动。确认输入与输出：

```bash
rostopic type /camera/image_raw
rostopic hz /camera/image_raw
gst-launch-1.0 rtspsrc location=rtsp://127.0.0.1:8554/usb_cam latency=100 \
  ! rtph264depay ! avdec_h264 ! autovideosink
```

如果输入是压缩图像，配置示例为 `/camera/image_raw/compressed` 和 `sensor_msgs/CompressedImage`。话题实际类型必须与 YAML 完全一致，否则 ROS 不会建立订阅。

`config/realsense_d435i.yaml` 仅提供 RealSense 彩色话题 profile；本包不启动 `realsense2_camera`。先由相机驱动发布 `/camera/color/image_raw`，再启动 `epgeneral_realsense_d435i_rtsp.launch`。

RTSP 服务面向可信局域网，不提供认证、TLS、录制或音频。防火墙需允许 TCP 8554；若日志持续显示等待帧，优先检查话题名称、类型和发布频率。
