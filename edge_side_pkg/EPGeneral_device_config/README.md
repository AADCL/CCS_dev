# epgeneral_device_config

端侧设备身份的唯一配置来源。`config/device.yaml` 中的设备 ID 和 IP 必须与地面站 `config/devices.json` 对应记录完全一致。mqtav 和 `epgeneral_video_srt` 均通过 roslaunch 参数读取此文件。

当前包版本：`v0.1.0`。
