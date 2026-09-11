# v0.24.0 验证证据

- `before-*.json` / `after-*.json`：同机 1、10、20 台回放原始指标及 RSS 采样。20 台各 600 秒；1、10 台各 30 秒，基线积压时额外排空最多 10 秒。
- `environment.json`：基线提交、解释器和实际依赖版本。两版使用同一环境，GPU 光栅化与真实网络未纳入回放。
- `test-summary.json`：完整隔离回归原始结果与修复后复验；最终 434 项中 432 项通过，2 项 Windows CRLF 基线失败。
- `v024-regression.log` 与两份详情页日志：新增 24 项回归与修复后复验。
- `source-sha256.json`：交付实现源码、验证脚本统一 LF 后的 SHA-256 校验值。

结论和复现步骤见 [发布验证记录](../../docs/RELEASE_VALIDATION.md)。这些文件仅包含模拟数据，不含现场设备或实际运动记录。
