# epgeneral_relocalization

配套 CCS 0.23.1：[完整使用手册](../documents/USER_MANUAL.md) · [设备内接口与参数](../documents/INTERFACE_REFERENCE.md)。包级 launch 默认读取共享配置包；一键脚本显式读取工作空间 `config/<profile>`，修改后需重启。

当前版本 v0.4.0。运行配置统一由 `epgeneral_device_config/config/relocalization.yaml` 提供。活动地图状态文件使用 schema 2：首次成功后原子保存 `map <- odom`，重复重定位前立即清除旧变换；进程重启时旧的 `localized` 状态自动降级为 `standby`，必须重新建立实时里程计和 TF。Ground-Air 和 Go2 profile 可按固定周期持续上报 `map <- odom`；Go2 还可配置定位健康话题，只有算法健康且 TF 有效时才报告成功。Ground-Air 可将协议中的 `public_map.pcd` 在原子安装前改名为定位器要求的 `cloud_map.pcd`，Scout/Wheeltec 继续使用原稳定窗口。

ROS1 常驻重定位协调包。它监听 `ccs-relocalization-v1` UDP 控制消息，从已配置的地面站 HTTP 地址续传地图 ZIP，严格校验 manifest、大小和 SHA-256 后原子安装，再按 profile 顺序启动 Scout FAST-LIO、坐标适配、全局 PCD 重定位和 map_server。

Scout profile 默认使用 `~/livox_fastlio/maps/ccs_download/<map_id>/`。Go2 只有在设备 profile 显式启用、配置真实定位 stages，并通过 `ros.localization_health_topic` 报告定位健康时才应开放；未完成设备算法接入的模板继续保持 `enabled: false` 并返回 `UNSUPPORTED_BACKEND`。

Scout/Wheeltec 的成功判据仍为连续稳定的 `map <- odom` TF；Ground-Air 的首个有效 TF 立即成功，并按固定周期上报最新或最后有效值。初始位姿发布至 `/initialpose`，结果和失败原因返回地面站；耐久日志位置由 profile 配置。

`start_stack` 接受可选 `replace_existing`。值为 true 或当前状态已经 localized 时，节点先取消旧 TF 监测、清除持久变换并反序停止旧进程组。监测代际会丢弃旧线程的迟到结果，新 TF 先写入端侧状态文件，再返回地面站覆盖绑定。
