# 按设备 ID 管理部署

更新日期：2026-09-12。新部署从[从零部署指南](../documents/DEPLOYMENT_GUIDE.md)开始，接口填写见[话题与服务清单](../documents/CONFIG_TOPIC_REFERENCE.md)，现场操作见[使用手册](../documents/USER_MANUAL.md)。

| 设备 ID | profile（运行文件不搬迁） | 已有端侧 | 唯一部署与验收记录 |
| --- | --- | --- | --- |
| QRD_001 | [go2_edu](go2_edu/) | nvidia@192.168.50.100 | [QRD_001](records/QRD_001/DEPLOYMENT.md) |
| QRD_002 | [go2_robot2](go2_robot2/) | unitree@192.168.50.111 | [QRD_002](records/QRD_002/DEPLOYMENT.md) |
| QRD_003 | [go2_robot3](go2_robot3/) | unitree@192.168.50.112 | [QRD_003](records/QRD_003/DEPLOYMENT.md) |
| UGV_001 | [scout_mini](scout_mini/) | nvidia@192.168.50.120 | [UGV_001](records/UGV_001/DEPLOYMENT.md) |
| UGV_003 | [wheeltec_r550p](wheeltec_r550p/) | nrc19@192.168.50.122 | [UGV_003](records/UGV_003/DEPLOYMENT.md) |
| AGV_001 | [ground_air_agv](ground_air_agv/) | bitcq@192.168.50.130 | [AGV_001](records/AGV_001/DEPLOYMENT.md) |

原 profile 的 README/DEPLOYMENT/VALIDATION 和 documents 中按机型拆分的部署/建图/定位/任务日志共 22 份材料已按 ID 合并；每份来源的原始 SHA-256 记录在合并文件开头。旧文档路径及章节锚点保留为跳转，今后不再同时维护多份现场记录。旧通用请求中的 QRD_002 历史也已归档，[请求模板](EDGE_DEVICE_DEPLOYMENT_REQUEST.md)恢复为新部署可填写内容。

目录约定：`deploy/<profile>/` 存可执行配置、脚本、launch 和辅助文件；`deploy/records/<设备ID>/DEPLOYMENT.md` 存该设备的概况和按日期保留的全过程。新增设备建立独立 ID 目录并更新索引，不通过修改另一设备身份来覆盖其历史。端侧 docs 目录保留自己的 profile 名，交付时同步这份按 ID 的记录。

历史里的“当前运行”不是实时状态。QRD_002/QRD_003 的适配器退出修复已于 9 月 12 日部署，见各自当天记录；QRD_003 的建图存储异常和重复启动锁修复仍只有本地验证，不能用旧哈希/PID 推断已部署。文档合并不表示重新部署或验收。

本轮 PR 审查还修复了普通卸载完成后新 PREPARE 无法重新准备的问题；真实 close 或 ROS 退出仍阻止新任务。这项后续修复只完成本地测试，尚未部署，不包含在 9 月 12 日记录的运行文件哈希中。

可复用步骤集中在 [GO2 部署经验](../documents/GO2_DEPLOYMENT_LESSONS.md)。2026-09-12 用户后续确认本轮落地测试已完成，见 [QRD_002](records/QRD_002/DEPLOYMENT.md#field-confirmation-20260912)、[QRD_003](records/QRD_003/DEPLOYMENT.md#field-confirmation-20260912) 的追加确认。保留原工具验收事实和历史清单，不以该反馈改写目标哈希或推断当前运行状态。

每次新记录必须包含日期、范围、源码版本/差异、安装清单、前后哈希、备份和证据路径、实测与未测项、日志位置、启停命令及回滚方法；保留失败及告警，回滚不覆盖更新的安全状态。密码和访问令牌不入记录。
