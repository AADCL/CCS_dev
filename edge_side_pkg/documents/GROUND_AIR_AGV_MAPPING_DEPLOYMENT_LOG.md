# Ground-Air AGV 建图部署日志

## 2026-09-01 `epgeneral_map_stream` v0.13.0 增量部署

- 目标设备：`AGV_001` / `192.168.50.130`；部署仅涉及建图响应，不下发车辆运动、解锁、模式切换或航点。
- 备份目录：`/home/bitcq/.deployment_backups/20260901T105207Z_ground_air_mapping_v013`。部署前控制 launch 为 `348669...30fe`，profile 为 `5f445d...0f35`；TF 修正前版本也保留于该目录。
- 原始 `manual_mapping.launch` 部署前后 SHA-256 均为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`；`save_mapping.launch` 为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`，均未修改。
- 最终新增 `manual_mapping_control.launch` SHA-256 为 `5f5be8916be43a0ce687dbbebbb1e3f35b275e4f6cee43db63161433d4d3986b`；AGV map-stream profile 为 `f8b501d4a1c0bca8342c0351535e192bf02557094d502a92ca2c3851f6cd9750`。
- 端侧目标测试 63 项通过、1 项按环境跳过；Python 编译、Bash 语法、版本一致性、launch 解析和 Release `catkin_make -j1 --force-cmake` 通过。Scout 专属 fixture 未部署在 AGV，因此未纳入目标增量套件。
- 首轮 12 秒和 60 秒静态测试验证了准备、FAST-LIO、预览、重复开始幂等及“保存失败保活”；保存失败原因为当前自启动未提供 `odom -> camera_init` 和 `body -> base_link`，导致动态栅格没有 `/map` 输出。测试均通过本会话 abort 清理，无建图节点残留。
- 将这两条静态 TF 加入新增控制 launch 并纳入预期节点检查后，完成两次静态闭环：`20260901_193200` 与 `20260901_193342`。两次均生成非空 PCD/PGM/YAML/metadata，下载归档 SHA-256 分别为 `4c397b102002134eb543e7a155217aafeb7595f3835291ea48468ec9aff265abc` 和 `8717a71455d6e90b809d84d013118995fe019ce307a72102b3a22f5e41581fed9`。
- 第二次闭环地图文件大小：`cloud_map.pcd` 318472 字节、`map.pgm` 47677 字节、`map.yaml` 120 字节、`metadata.json` 188 字节。结束后建图节点和 roslaunch 进程组均退出，MAVROS、Livox、MQTT、UDP 遥测、map-stream、A8 与 SRT 继续运行，UDP 14561 正常监听。
- 本地验收归档与事件记录保存在 `artifacts/agv_incremental_test/`；端侧地图保存在 `/home/bitcq/catkin_ws/maps/`。静态测试只验收流程与产物，不评价地图精度。
- 2026-09-02 补充“保存中以新 request ID 重复结束”处理：不再返回通用 map mismatch，而是幂等接受并回报当前 `generating`，不创建第二个保存线程。端侧 65 项目标测试通过、2 项按环境跳过，增量构建通过。
- 发现普通 `nohup` 启动仍关联 SSH 会话，断开后 supervisor 退出。现改为 `setsid -f` 脱离终端启动，实测 supervisor `PPID=1`、无 TTY；SSH 断开重连后全部常驻节点、UDP 14561 和 TCP 14600 继续在线。

## 2026-09-02 FAST-LIO 后置坐标转换 launch 增量部署

- 备份目录：`/home/bitcq/.deployment_backups/20260902T023030Z_mapping_tf_sequence`；备份了修改前的控制 launch、AGV profile、建图进程脚本和配置解析代码。
- 新增 `~/catkin_ws/src/car_bringup/launch/mapping_coordinate_transforms.launch`，只包含 `odom_frame/camera_init_frame/body_frame/base_frame` 四个参数，以及 `/odom_camera_init_broadcaster`、`/base_link_body_broadcaster` 两个静态 TF 节点。其 SHA-256 为 `0b647a527fc4a7f9cc397ebe7d9cd3f56ae3e260139e06c74dd9368b414a6553`。
- `manual_mapping_control.launch` 不再内嵌 TF 节点，SHA-256 为 `627eae854fd0c5ef4b177bc4728bde9e613db34b22df9e32d456318ca188970b`。进程管理流程调整为：启动该控制入口，等待 `/fast_lio_node`，再启动独立坐标转换 launch，最后等待全部建图节点就绪；两个 roslaunch 仍属于同一受控进程组。
- 原始 `manual_mapping.launch` SHA-256 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`，均未修改。
- 端侧 Bash/Python/XML/launch 解析通过；AGV 目标套件运行 65 项，63 项通过、2 项按环境跳过；Release 增量构建通过。全量发现的 7 个 Scout fixture 错误仅因 AGV 未部署 `/home/nvidia/ccs_edge_ws/config/scout_mini`，与本次变更无关。
- 12 秒静态闭环地图目录为 `/home/bitcq/catkin_ws/maps/20260902_104551/`：`cloud_map.pcd` 320764 字节、`map.pgm` 44096 字节、`map.yaml` 118 字节、`metadata.json` 188 字节。地面站验收归档为 `artifacts/agv_incremental_test/tf_sequence_20260902/agv-static-20260902-104545.zip`，大小 290786 字节，SHA-256 为 `03bd12332f11ebf69e9e7b5b5065cda82b29205f061815f5edfac853110803c7`。
- 会话日志顺序为 `stage=ground_air_mapping action=start`、`stage=coordinate_transform action=start ... after_node=/fast_lio_node`、`stage=ground_air_mapping action=ready`。保存成功后无 FAST-LIO、坐标转换或建图 roslaunch 残留，MAVROS、Livox、MQTT、UDP 遥测、map-stream、A8 和 SRT 均继续运行。
- 统一 supervisor PID 9897 保持 `PPID=1` 且无 TTY；SSH 断开重连后 `/epgeneral_map_stream`、UDP 14561 和 TCP 14600 均继续在线。

## 2026-09-02 坐标转换改为开机常驻

- 将 `mapping_coordinate_transforms.launch` 的所有权从建图会话移至 `~/ccs_edge_ws/start_ccs_edge_dev.sh`。统一启动脚本在其他业务节点前启动 `mapping_tf`，等待 `/odom_camera_init_broadcaster` 和 `/base_link_body_broadcaster` 均就绪后才继续。
- 建图会话恢复为只持有 `manual_mapping_control.launch`。AGV profile 不再配置坐标转换 launch 或 FAST-LIO 后置门槛；建图预期节点仍检查两条常驻 TF，建图结束不会停止它们。
- 新增并启用 systemd 用户服务 `~/.config/systemd/user/ccs-edge-dev.service`，`Restart=on-failure`；用户 linger 已启用。验收时服务为 `enabled/active`，`NRestarts=0`，SSH 断开后仍运行。
- 仅用 `setsid` 临时拉起时，SSH 被端侧重置后服务消失，首次静态重测返回 `timed out waiting for expected mapping response`。确认端侧原无启用的 CCS 开机服务后，改由 systemd 用户服务监管并重新测试通过；未恢复已禁用的旧桌面整车自启动入口。
- 部署后 `start_ccs_edge_dev.sh` SHA-256 为 `0888f3bd8abb23ffddc19a4f9c6a9b9a6c5eb4984749be866b358a0bf723b1e2`，AGV map-stream profile 为 `9f407bea824e0b4f07a06b859a6f7c8504a94b4f8348d4c95cbd67157cf3a6b5`。回滚备份位于 `/home/bitcq/.deployment_backups/20260902T_tf_autostart`。
- 端侧目标套件运行 65 项，63 项通过、2 项按环境跳过；Bash/Python/YAML/systemd unit 校验和 Release 增量构建通过。
- systemd 接管后完成 12 秒静态闭环，地图目录为 `/home/bitcq/catkin_ws/maps/20260902_150431/`：`cloud_map.pcd` 322132 字节、`map.pgm` 45837 字节、`map.yaml` 118 字节、`metadata.json` 188 字节。地面站归档为 `artifacts/agv_incremental_test/tf_autostart_20260902/agv-static-20260902-150427.zip`，大小 292631 字节，SHA-256 为 `adcb14879accac21ccb0afeb7fcfbb2236957083068968f953ab84df430af0a7`。
- 建图结束后无 FAST-LIO 或建图 roslaunch 残留；两个 TF 节点继续存在，`odom -> camera_init` 与 `body -> base_link` 均实测为零平移、单位四元数。原始 `manual_mapping.launch` 和 `save_mapping.launch` 校验值保持不变。
- 按端侧仅增量测试要求，未执行整机重启；通过 systemd `enabled/active`、linger、SSH 断开重连和建图闭环验证开机服务配置与运行保持性。
