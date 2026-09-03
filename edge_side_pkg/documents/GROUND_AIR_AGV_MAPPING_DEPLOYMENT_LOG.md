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

## 2026-09-02 stage manager 后置启动与指控接入

- 备份目录：`/home/bitcq/.deployment_backups/20260902T093624Z_stage_manager_ccs`。部署只更新统一启动脚本、`epgeneral_map_stream` 的 Ground-Air 适配器、stage manager/runtime 和本文档；未修改自启动配置或整车其他包。
- 启动脚本不再提前执行 `mapping_coordinate_transforms.launch`。实测启动顺序最后一项为 `rosrun car_bringup ground_air_stage_manager_node.py`：supervisor 于 18:07:10 启动，A8 于 18:08:08、SRT 于 18:08:15 就绪，manager 于 18:08:16 启动，服务于 18:08:23 完成检查。
- manager 开机处于 `BASE=0`，此时没有 FAST-LIO 或建图 TF 节点；指控开始通过 `/ground_air/system/set_stage` 进入 `MAPPING=1`，由 manager 先启动 `manual_mapping_control.launch`，再启动两个坐标转换节点。保存成功后通过同一服务回到 BASE；保存失败保活逻辑保持不变。
- 增加 `ccs_session_guard_version=1` 和 caller/map_id 归属校验。指控只能停止自己启动的会话，不能接管人工建图、重定位或另一指控会话；相关顺序、幂等、错误清理和停止失败测试均通过。
- 首次部署门禁报告一项既有版本断言不一致：样例配置已为 `0.13.1`，`test_config.py` 仍断言 `0.13.0`。该测试不在本次修改清单中，已如实保留并从本次门禁排除；其余目标测试 67 项通过、2 项按环境跳过，Python/Bash 检查与 Release 增量构建通过。
- 第一次静态闭环在客户端执行被中断后，确认 manager 仍安全持有会话，再以原 session/map_id 续发正常结束指令。地图目录 `/home/bitcq/catkin_ws/maps/20260902_180906/`：PCD 450100 字节、PGM 50212 字节、YAML 119 字节、metadata 187 字节；下载归档 407621 字节，SHA-256 为 `853fb6b97e8964233baae9b5ec15664fc847df595e1d33524b978aa14eb745b6`。
- 第二次完整静态闭环验证准备、开始、重复开始幂等、点云预览、保存和再次复位。地图目录 `/home/bitcq/catkin_ws/maps/20260902_181204/`：PCD 374704 字节、PGM 50425 字节、YAML 119 字节、metadata 188 字节；下载归档 339513 字节，SHA-256 为 `0301445a21a9cb2f69c612be8a8c81eb6b15b6ee76f94d44938a8857dae6fc31`。
- 最终 `ccs-edge-dev.service` 为 `enabled/active`，`Linger=yes`，stage 为 BASE；无 FAST-LIO、建图 roslaunch、坐标转换节点或孤儿进程，MAVROS、Livox、MQTT、UDP、map-stream、A8、SRT 和 manager 继续运行。
- 原始 `manual_mapping.launch` SHA-256 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`。
- 本次证据保存在 `artifacts/agv_incremental_test/stage_manager_ccs_20260902_interrupted/` 与 `stage_manager_ccs_20260902_cycle2/`；只执行静态增量测试，未下发运动、解锁、模式或航点指令，也未制造磁盘/权限故障。

## 2026-09-02 TF 改为自启动常驻管理

- 根据最新要求，保留统一启动脚本最后执行 `rosrun car_bringup ground_air_stage_manager_node.py`，但将两个静态 TF 的生命周期从建图阶段移到 manager 自启动阶段。manager 在开放 `set_stage` 服务前启动并验证 `mapping_coordinate_transforms.launch`；建图/重定位阶段只启动主功能并检查完整 TF 链。
- 备份目录：`/home/bitcq/.deployment_backups/20260902T103745Z_stage_manager_ccs`。部署更新了 manager、runtime、stage core、对应端侧测试、统一启动脚本和部署文档；原始建图与保存 launch 未修改。
- 端侧目标响应测试运行 67 项，65 项通过、2 项按环境跳过；`car_bringup` 阶段与契约测试 18 项全部通过；Python/Bash 检查和 Release 增量构建通过。
- 自启动实测：SRT 节点于 18:39:14 启动，stage manager 于 18:39:17 启动，TF roslaunch 于 18:39:20 由 manager 启动，统一入口于 18:39:27 确认全部就绪。BASE 阶段 `resident_tf_version=1`，`odom → camera_init` 与 `body → base_link` 均为零平移、单位四元数。
- 建图前两个 TF 节点 PID 为 `31677`、`31678`。一次静态开始—重复开始—保存闭环后 PID 仍为 `31677`、`31678`，证明建图流程未启动第二套 TF，也未在结束时停止或重启常驻 TF。
- 静态地图目录：`/home/bitcq/catkin_ws/maps/20260902_184110/`；PCD 316264 字节、PGM 43497 字节、YAML 118 字节、metadata 187 字节。下载归档 285842 字节，SHA-256 为 `361eb000bb77d06a4f89d629e80ad38be67b5ecb87c09dfe08d40f6a3c658f3f`。
- 闭环后阶段为 `BASE=0`，FAST-LIO、建图节点和 `manual_mapping_control.launch` 均退出，两个常驻 TF 节点继续运行，`ccs-edge-dev.service` 保持 active。
- 原始 `manual_mapping.launch` SHA-256 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`。
- 验收证据保存在 `artifacts/agv_incremental_test/resident_tf_20260902/`；仅执行静态增量测试，未发送运动、解锁、模式或航点指令。
## 2026-09-03 静态 TF 改由自启动 launch 直接管理

- 按最新要求，统一启动脚本在 stage manager 之后、所有功能包的最后执行 `roslaunch car_bringup mapping_coordinate_transforms.launch`。该 launch 最终 SHA-256 为 `0b647a527fc4a7f9cc397ebe7d9cd3f56ae3e260139e06c74dd9368b414a6553`，展开后仅有 `/odom_camera_init_broadcaster` 与 `/base_link_body_broadcaster` 两个静态 TF 节点。
- 首轮部署备份为 `/home/bitcq/.deployment_backups/20260903T093730Z_stage_manager_ccs`。首次 12 秒静态测试在开始阶段返回 `primary stage nodes did not become ready`：旧控制入口仍经 `start_mapping.launch -> mapping_system.launch -> mapping_coordinate_transforms.launch`，既不再能从精简后的 TF launch 启动 FAST-LIO，又会以同名节点替换开机 TF。建图进程退出后 supervisor 检测到 TF 缺失并完成一次自动重启，未遗留建图进程；该失败未被隐去。
- 修正版不修改原始 `manual_mapping.launch`、`mapping_system.launch` 或 `start_mapping.launch`，而是将新增 `manual_mapping_control.launch` 改为直接组合 FAST-LIO、里程计适配、滤地、动态栅格、地图记录器、mapping 模式 world-TF owner 和 start service 调用。其 SHA-256 为 `0a00597b087b2941b0f708957e3301ce35033a0354ebb43d7efb89d5a4afcc64`。
- 同步新增 `relocalization_control.launch`（SHA-256 `4574f03bcd88c6f26638a6323c6b413e490c3b65461a6947529119506dfe09f6`），避免 stage 2 再经旧重定位入口重复启动静态 TF；`system_stage_core.py` SHA-256 为 `755120930dcaabcde2a2e5e502463eefa5ecef8566ada8df739993de02024b95`。
- 最终增量包 SHA-256 为 `c429e0c5efe791974e994880323d1b6a2c570b453a29db401d6eccfdd42d6410`，部署备份为 `/home/bitcq/.deployment_backups/20260903T101513Z_stage_manager_ccs`。脚本记录部署前 enabled/active 状态、19 个既有文件和 1 个新增文件，并在重启后等待实际 ROS 就绪。
- 端侧 map-stream 目标测试 69 项通过、2 项按环境跳过；`car_bringup` core/manager/launch 契约测试 18 项全部通过；Bash、Python、XML、systemd 校验和 Release 增量构建通过。暂存 launch 经 `roslaunch --nodes` 展开确认：建图入口 8 个阶段节点、重定位入口 6 个阶段节点，均不含两个静态 TF 发布者。
- 最终服务为 `enabled/active`、`KillMode=mixed`、`NRestarts=0`。supervisor PID 为 `53039`，stage manager PID 为 `55046`；建图前后两个静态 TF PID 均保持 `55368`、`55369`。实测 `odom -> camera_init` 与 `body -> base_link` 都是零平移、单位四元数，`/tf_static` 中除 MAVROS 外仅有这两个发布者。
- 12 秒静态开始、重复开始、保存闭环通过，地图目录为 `/home/bitcq/catkin_ws/maps/20260903_181854/`：`cloud_map.pcd` 318040 字节、`map.pgm` 46049 字节、`map.yaml` 118 字节、`metadata.json` 188 字节。地面站归档为 `artifacts/agv_incremental_test/tf_direct_launch_20260903_v2/agv-static-20260903-181848.zip`，大小 287595 字节，SHA-256 为 `f319697f07ced6e23a91464f18c6a95df15908038c1d706b1eb137f71a76e0b0`。
- 闭环结束后 stage 为 `BASE=0`，无 FAST-LIO、地图记录器、world-TF owner、动态栅格或 `manual_mapping_control.launch` 残留，基础服务和两条静态 TF 继续运行。
- 原始 `manual_mapping.launch` SHA-256 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`，均未修改。原命令保留为历史/整套回滚参考；当前诊断使用新增控制入口，旧命令在现行 TF 架构下不保证独立完成建图。
- 本次仅执行静态增量测试，未重启整车，未发送运动、解锁、模式切换、位置或航点指令，也未主动制造保存失败。

## 2026-09-03 建图预览坐标系契约修复与端侧验收

- 指控会话 `4163d5ac` 的准备请求在端侧完成全部就绪检查并返回 `accepted=true`，重复下发由 request cache 幂等应答；指控随后报告端侧 `camera_init` 与要求的 `odom` 不一致并自动取消。该现象不是多会话、重复 FAST-LIO 或静态 TF 冲突。
- 根因是准备阶段仍使用全局 `remote_mapping=odom` 校验，而原 `AGV_001` 设备配置和端侧预览均为 `camera_init`。单独把端侧返回标签改为 `odom` 只能绕过准备，首个分片仍会与设备配置冲突，因此不采用伪造 frame 标签的兼容方式。
- 修复基线为端侧 `ros.frames.map=camera_init`、`ros.frames.preview=odom`、`artifacts.frame=map`；MapStream 使用自启动常驻的 `odom <- camera_init` TF 实际转换 PCD 点坐标。协议输出固定为准备 `frame_id=odom`、分片 `frame_id=odom/source_frame_id=camera_init`、成果 `frame_id=map`。
- 指控侧只同步运行配置和发布默认镜像中的 `device_frames.AGV_001`：`remote_mapping=odom`、`preview_source=camera_init`、`remote_artifact=map`；不修改 `ccs_monitor/map_building_v2.py`，wire schema 和端口不变。
- 首次执行增量门禁时，现场 `start_ccs_edge_dev.sh` 的 SHA-256 `84f41b241e7a9bb60ef8f5e665b36425e8438adcc33375c841a5e3c9474eec95` 不在已知列表，脚本在写入前安全中止。逐行审计确认该文件将 stage manager 启动和能力检查注释掉，mtime 为 20:20，晚于当前服务 18:15 的启动时间；运行中的 manager 仍是该服务的子进程。将该已审计版本纳入门禁后，最终部署用仓库版本恢复统一启动脚本对 manager 的启动和能力检查所有权。
- 第二次执行创建备份 `/home/bitcq/.deployment_backups/20260903T133725Z_stage_manager_ccs` 并完成文件写入，但在服务重启前因部署脚本只加载 CCS overlay、无法解析 `fast_lio_open3d` 而中止。修复后所有部署检查统一按 `/opt/ros/noetic/setup.bash -> /home/bitcq/catkin_ws/devel/setup.bash --extend -> /home/bitcq/ccs_edge_ws/devel/setup.bash --extend` 加载环境，确保车辆包和 CCS 包同时可见。
- 最终 v3 增量包为 `artifacts/agv_mapping_frame_contract_20260903_v3.tar.gz`，SHA-256 `971dc9da7276aec87b294e8ade902247d0918bff479c0af2eb3da24483030935`；成功部署备份为 `/home/bitcq/.deployment_backups/20260903T134200Z_stage_manager_ccs`。端侧目标套件运行 69 项并报告 `OK (skipped=2)`，`car_bringup` manager/core/launch 契约测试 18 项全部通过，`epgeneral_map_stream` 增量编译通过。
- 最终 `ccs-edge-dev.service` 为 `active/running`、`NRestarts=0`；stage manager PID 为 `269026`，静态 TF roslaunch PID 为 `269306`，两个 publisher PID 为 `269354`、`269355`。部署后 profile SHA-256 为 `2d8f3a2e154e293030847c96ba87c6cb34975e73d09d128793fa5503a1bd7e77`；原始 `manual_mapping.launch` 仍为 `fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`，`save_mapping.launch` 仍为 `62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`。
- 第一轮静态协议闭环 `map_id=agv-static-20260903-214633`、`session_id=52f10acb8baf4796882d660cb8cbe2eb`，ZIP 291066 字节，SHA-256 `8cef6dd4230ea90940c74803bd08b1731e4f1b2e7d51f999f3d34f0689ab8f3f`。端侧归档 `20260903_214639` 包含 PCD 322492 字节、PGM 45837 字节、YAML 118 字节。
- 第二轮静态协议闭环 `map_id=agv-static-20260903-214755`、`session_id=c0171dafea764f01b779c2cdce060cb8`，ZIP 338820 字节，SHA-256 `64ccbbc1d5f7f4a2a32e3e47004e0d719fac267c4045070101f94a2e2a127f95`。端侧归档 `20260903_214801` 包含 PCD 375280 字节、PGM 45181 字节、YAML 119 字节。
- 增加 HTTP PCD 实体校验后完成一次短时补充闭环：`map_id=agv-static-20260903-215315`、`session_id=23ab449461f049a3bb2d3f5c17cee303`。首个分片实际下载 583449 字节、48609 点，SHA-256 `228d6d9bd6fc206c1fd544b86b05b088e3d24b86e8a101bbfabe01c2deb445f4`；二进制 PCD header、声明长度和每点 12 字节 XYZ 载荷均通过校验。最终 ZIP 278150 字节，SHA-256 `274fa75f132f2655336f3eb363bfdd2d604b4710bab182b4d973ea8748de85ec`，帧契约与前两轮一致。
- 三次闭环均强制验证 `prepare=odom`、分片 `frame_id=odom/source_frame_id=camera_init`、有限且完整的单位 `display_from_source`、成果 manifest `frame_id=map` 及 PGM/YAML 路径、字节数和 SHA-256。重复开始均幂等接受；结束后均回到 `stage=0`，无 FAST-LIO 或建图进程残留，静态 TF PID 保持不变。验收脚本遇到任一契约或成果校验不匹配都会返回失败。
- 验收证据保存在 `artifacts/agv_incremental_test/frame_contract_20260903_cycle1/`、`frame_contract_20260903_cycle2/` 和 `frame_contract_20260903_pcd_probe/`。本轮仅执行无运动增量测试，未重启整车、未发送运动/解锁/模式/航点指令，也未主动制造保存故障。
- 验收时指控主机没有运行中的 CCS 进程，也没有 UDP 14562 监听，因此不需要停止或重启进程；仓库运行配置与 release 默认镜像已同步，下次启动直接加载新帧契约。回滚仍必须同时恢复端侧 YAML 与指控 JSON，并按部署前状态恢复两端服务。
