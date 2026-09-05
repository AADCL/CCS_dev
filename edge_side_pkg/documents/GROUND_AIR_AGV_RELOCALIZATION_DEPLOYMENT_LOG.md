# Ground-Air AGV 重定位部署日志

## 2026-09-05 建图兼容回归修正

- 联合建图暴露出 9 月 4 日重定位升级的集成缺口：manager 和启动门禁已升为 guard `2`，map-stream 客户端仍精确要求 `1`。原重定位闭环结果保持有效，但不能代表建图准备阶段已通过。
- `epgeneral_map_stream` v0.13.2 明确支持整数 guard `1`、`2`；后续重定位增量包同步客户端、关联元数据和回归测试，并在实际就绪门禁中执行建图 `--check`。
- 本次兼容修复保持 manager、重定位协议、常驻 TF 和已禁用的上电自启动设置。端侧聚焦测试、实际建图预检及一次无运动建图闭环均已通过，完整备份、地图校验值与最终清理状态统一记录于 [建图部署日志](GROUND_AIR_AGV_MAPPING_DEPLOYMENT_LOG.md)；以下 9 月 4 日重定位历史结果保持不变。


## 2026-09-04 实施记录

- 目标：启用 `AGV_001` 两阶段重定位，并以固定 1 秒周期持续上报 `map <- odom`。
- 边界：端侧文件写入仅允许 `/home/bitcq/ccs_edge_ws`；车辆 underlay 和用户服务定义保持只读。
- 自动验收地图：`test60`；初始位姿 `(0, 0, 0)`。
- 最终增量包：`agv-relocalization-incremental-v6.tar.gz`，大小 102149 字节，SHA-256 `12814a3ca5c6ac6c1e53f09a1c684c76a35d6c03f4bc98e3480b667cca53c052`。
- 最终部署批次：`20260904T101248Z_agv_relocalization`；备份位于 `/home/bitcq/ccs_edge_ws/.deployment_backups/20260904T101248Z_agv_relocalization`，证据位于 `/home/bitcq/ccs_edge_ws/artifacts/relocalization_acceptance/20260904T101248Z_agv_relocalization`。

## 问题与修正

- 首次端侧启动检查使用了其他 profile 的 `/map_2d`，而 Ground-Air map server 实际发布 `/map`；修正 profile 后栈进入等待初始位姿。
- 第二次零位姿调用被定位器明确拒绝：`active point-cloud map must be the processed cloud_map.pcd`。协议只允许 `public_map.pcd`，Ground-Air 地图注册器又要求目录仅一个 PCD。本次保留 wire 校验，端侧原子安装前将 PCD 改名为 `cloud_map.pcd`，不修改 underlay。
- 根据静止设备行为，首个有效 TF 后改为每秒优先采新样本；没有新时间戳或查询暂时失败时重发最后有效值，不再因设备不移动判定连续缺失。

## 验收结果

- 最终端侧部署脚本运行通用重定位 22 项和 Ground-Air 控制 7 项测试，增量编译两个包并验证覆盖 launch；平台及发布聚焦测试 54 项通过，`git diff --check` 通过。
- `test60` 的实际 `map_id` 为 `a60133b8-9915-4f08-8139-3483f4cfbdb9`。端侧安装结果仅包含 `cloud_map.pcd` 864496 字节、`map.pgm` 50139 字节和 `map.yaml` 123 字节。
- 两轮零位姿闭环均通过，每轮接收 11 个 `map <- odom` 成功结果，中位间隔均为 1.000 秒，协议告警为 0。结果中既有新样本，也有静止周期的重复值。
- 两轮质量门禁分别为 fitness `0.9922438345` / `0.9924242424`，RMSE `0.0454686665` / `0.0459282188`，无需人工选点或降低阈值。
- v6 追加两轮重复开始验收：第二次开始约 40 ms 即回到等待位姿，manager 日志仅有 1 次受管 stage 启动；两轮各收到 4 个结果，中位间隔 0.938 / 1.000 秒，fitness `0.9923991877` / `0.9923553599`，RMSE `0.0453172890` / `0.0453244340`。
- 测试结束后重启既有 `ccs-edge-dev.service` 释放受管进程组。最终 stage 为 `BASE`、guard 为 2、服务 `active/running`、`NRestarts=0`；无 FAST-LIO、定位器、地图服务或重定位 launch 孤儿节点，常驻 MAVROS、Livox、阶段管理器、重定位响应和两条静态 TF 正常。
- underlay 四个 launch 部署前后 SHA-256 一致：`manual_mapping.launch=fbc332ac343f6c72f232de176db669738110f850ca77a3285d6dc789efc56326`、`save_mapping.launch=62cd3592256fd77b0c87001c69293c1034d088875e663d4e72a284b3eeea8f52`、`relocalization_system.launch=0df0e8480dc20ceea346f9c6b7d3b6a749b91639fc329f1d208ad346d8ea5909`、`mapping_coordinate_transforms.launch=0b647a527fc4a7f9cc397ebe7d9cd3f56ae3e260139e06c74dd9368b414a6553`。
- 本机生产 CCS 在隔离验收前正常关闭，验收后恢复，UDP 14566 与 TCP 14601 均由同一进程监听。全程未下发运动、解锁、模式或航点指令，也未在 `/home/bitcq/ccs_edge_ws` 外写入端侧文件。
- 完整平台套件共 352 项，350 项通过；剩余 2 项为最新 `main` 已存在的日间主题颜色映射缺口和 Wheeltec 默认 profile 断言，与本次文件无交集。对应本次范围的聚焦回归全部通过。
