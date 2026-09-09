# Go2 Robot 2 端侧部署

本目录只对应 `QRD_002 / 192.168.50.111`，原生工作空间为
`/home/unitree/go2_nav_ws`，CCS 工作空间为 `/home/unitree/ccs_edge_ws`。
其他设备继续使用各自 profile，不部署本目录覆盖旧 `go2_edu`。

- [部署结构、日常启动与原生 launch 参数](DEPLOYMENT.md)
- [本次真实部署结果、增量测试、已知状态与备份校验值](VALIDATION.md)
- [一次性迁移、最终清理与回滚用法](VALIDATION.md#迁移与回滚用法)
- [迁移脚本](migrate_workspace.sh)

当前物理目录迁移与 `vendor/bin` 清理已经完成；日常只需使用工作空间根
`start_ccs_edge_dev.sh`，不要重复执行一次性迁移。真实运动尚未验收。
