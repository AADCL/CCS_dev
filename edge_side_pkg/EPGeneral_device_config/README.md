# epgeneral_device_config

配套 CCS 0.23.1：[完整使用手册](../documents/USER_MANUAL.md) · [设备内接口与参数](../documents/INTERFACE_REFERENCE.md)。包级 launch 默认读取共享配置包；一键脚本显式读取工作空间 `config/<profile>`，修改后需重启。

端侧公共配置的唯一入口。`config/` 保存设备身份以及 MQTT、UDP 遥测、视频、建图、重定位和任务配置；其他功能包不再携带运行 YAML。`device.yaml` 中的设备 ID 和 IP 必须与地面站 `config/devices.json` 对应记录完全一致。

当前包版本：`v0.1.1`。

单包默认入口使用本目录；设备一键脚本使用 `<CCS工作空间>/config/<profile>`。部署前将 profile 同名 YAML 安装到实际入口。普通设备安装本包与六个业务包，Ground-Air 另加专用控制包及 ground_air_msgs；deploy/documents 不进入 catkin src。
