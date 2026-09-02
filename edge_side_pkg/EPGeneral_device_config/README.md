# epgeneral_device_config

端侧公共配置的唯一入口。`config/` 保存设备身份以及 MQTT、UDP 遥测、视频、建图、重定位和任务配置；其他功能包不再携带运行 YAML。`device.yaml` 中的设备 ID 和 IP 必须与地面站 `config/devices.json` 对应记录完全一致。

当前包版本：`v0.1.1`。

部署前使用 `deploy/<profile>/config/` 中的同名文件覆盖本目录；只将本包与六个主功能包复制到端侧，不复制 `deploy` 和 `documents`。
