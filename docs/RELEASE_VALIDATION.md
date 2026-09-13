# 发布验证记录

## v0.24.1 指控大屏增量验证

日期：2026-09-13。基于 main `184575bb5ca99a014cf7d6088aa019092fd2a52d`，在独立分支 `codex/dashboard-visual-polish` 实施设备卡片、实时状态、控制台和任务图层修复。

本轮仅运行相关增量测试与合成数据的原生 OpenGL 检查，不执行全量测试、不升级依赖、不连接真实设备。环境版本、基线、逐项结果、复现命令和前后截图统一记录于 [dashboard-polish](validation/dashboard-polish/README.md)。

## v0.24.0 源码迭代验证

日期：2026-09-11。基线 `aa55385e6d4bd90f32463b6ddccf5bdb59faa369`，独立分支 `codex/v0.24.0-multi-device-tasks`；现场设备配置和其他任务的工作树未纳入变更。

- 针对性测试及完整地面站回归、修复后复验：**434 项，432 项通过，2 项基线失败**。无跳过项。包含多设备依次/同步下发、乱序/重复/旧请求、失败重试、并发保存、锁外通知、全体就绪门禁、启动 revision/UTC 稳定、重连协商、轨迹持久化与损坏文件、目录不可写、显示去重不删采集点、清理接口和图层偏好。
- 两项剩余失败为 `test_go2_robot3_profile` 中 `test_ros_contract_probe_is_python38_and_cannot_name_production_endpoints` 与 `test_scripts_are_linux_text_and_parse`，均因本机 Windows 检出 CRLF；在干净 main 同样复现，未混入独立 Go2 任务的修复。
- Windows 单进程完整发现曾遭遇无显示 OpenGL 原生退出；改用 `scripts/run_isolated_tests.py`，各模块单独进程，`test_ui` 再按用例隔离。初次隔离跑出的两项界面刷新回归修复后单独通过，保留原始汇总与复验结果。
- 实际环境为 Windows 11 (10.0.26200)、Python 3.12.14、PySide6 6.11.1、NumPy 2.5.1、VisPy 0.16.2。优化前后使用同一解释器及依赖；这不是仓库锁定发布环境的打包验收。语法编译和 `git diff --check` 通过。

### 同机性能对比

20 台各回放 600 秒；基线积压时最多额外排空 10 秒。1、10 台各 30 秒作规模对比，不作为长期稳定性结论。每台 MQTT 心跳/状态各 1 Hz，UDP 分层 20/5/1 Hz 与心跳 1 Hz；临时两天电池历史、1000 点 PCD 和多设备任务。20 台测试每 30 秒轮换任务页、大屏、设备页。

| 设备数 | 版本 | 运行秒数 | UI P95 / P99 (ms) | CPU（一个核=100%） | RSS 峰值 / 预热后增量 (MiB) | 回放队列丢弃 / 剩余 | 隐藏标记绘制调用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | main 基线 | 30.0 | 4.37 / 37.25 | 20.0% | 151.87 / 1.64 | 0 / 0 | 121 |
| 1 | v0.24.0 | 30.0 | 0.87 / 1.26 | 7.7% | 143.52 / 1.23 | 0 / 0 | 0 |
| 10 | main 基线 | 40.3 | 150.10 / 233.36 | 98.3% | 195.55 / 14.66 | 0 / 7501 | 400 |
| 10 | v0.24.0 | 30.1 | 2.33 / 6.89 | 16.1% | 161.48 / 4.55 | 0 / 0 | 0 |
| 20 | main 基线 | 610.0 | 422.29 / 607.30 | 98.3% | 229.14 / 10.85 | 322437 / 19863 | 3848 |
| 20 | v0.24.0 | 600.0 | 28.03 / 60.15 | 43.5% | 219.88 / 7.49 | 0 / 0 | 0 |

20 台优化版共处理 MQTT 24000 条、UDP 324000 条。UI P95 ≤100 ms、P99 ≤250 ms，预热后 RSS 增量 ≤32 MiB 或预热起点的 10%（取较大值），以及零丢弃/积压和隐藏标记绘制均通过本次回放。UI 指标为 10 ms Qt 定时器的事件循环延迟；内存增量取预热后最大 RSS 相对预热起点，20 台预热 120 秒。基线丢弃为有界回放队列满后的计数，不等同真实网络丢包。

测量包含真实 MQTT 解析、UDP 编解码/快照、Qt 控件、VisPy CPU 场景和网格构造；不含 GPU 光栅化、真实 MQTT Broker/UDP 网络、无线干扰或设备运动。未进行真实 20 台设备、完整显卡绘制、锁定发布环境及安装包验收。图形开关/内存释放的自动化用例通过，不能据此宣称实际显卡内存占用已经验收。

原始数据、运行环境及完整测试摘要：[artifacts/v0.24.0](../artifacts/v0.24.0/)。复现命令：

```powershell
python scripts/run_isolated_tests.py
python scripts/benchmark_multi_device.py --source-root <clean-main-checkout> --devices 20 --seconds 600 --output build/performance/before-20.json
python scripts/benchmark_multi_device.py --devices 20 --seconds 600 --output build/performance/after-20.json
```

## v0.23.1 增量验证

日期：2026-09-05。产品补丁版本为 0.23.1，ROS 包版本保持不变，发布渠道为 pre-release-v0.23.1。

- 配置参考覆盖默认七份 YAML 和四套 profile 共 35 份文件的叶级参数；核对公开 launch 参数及 CCS_* 脚本环境变量。
- 检查当前文档链接/锚点、八包 README、产品版本和包版本，验证实际端侧/便携 ZIP 中的文档及相对链接。
- 增量运行版本、端侧布局/配置/视频配置、发布工程、设备配置、地图上下文和 Ground-Air profile 测试；命令为：
- 上述集合在 Windows / Python 3.10.19 发布环境中共 67 项，全部通过；日志保存在构建工作区 build/incremental-validation.log。
- PR #33 由独立审核代理检查完整差异，发现 Ground-Air 从端侧 ZIP 首次安装缺少服务、launch/override 和源脚本权限步骤。使用手册及专项部署入口已修正，代理复核无剩余阻塞项；修正后文档及发布工程相关 17 项检查全部通过。该记录是代理代码审核，不代表 GitHub 同账号独立批准。
- 最终安装冒烟发现 Windows 原有未锚定的 config/data 排除规则漏装第三方库中的同名目录，导致文件清单不一致；规则改为根目录匹配。最终安装器按清单哈希、文档链接、真实应用启动及 0.23.0 升级保留数据重新验证，结果以 Release 记录为准。
- 清理临时 Linux 安装时发现 Windows 归档保留 CRLF，导致卸载脚本无法执行；分发复制阶段统一 Shell/Python 脚本为 LF，并检查两类 ZIP 的脚本行尾。最终产物需重新构建并验证安装、升级、启动及卸载保留数据，未公开的候选产物不作为发布结果。

~~~bash
python -m unittest tests.test_edge_documentation tests.test_version tests.test_edge_package_layout tests.test_edge_config tests.test_edge_video_config tests.test_release_engineering tests.test_device_config tests.test_device_map_context tests.test_ground_air_agv_profile -v
~~~

最终四类产物从合并后的 main 提交重新构建。构建提交、文件 SHA-256、安装/启动冒烟结果及未验证项记录于对应 GitHub Release，不能把下述 v0.23.0 的测试结果计作本次重测。未执行真实设备部署、运动控制或四系统完整实机验收。

## v0.23.0 历史发布验证

验证日期：2026-09-03。下表记录已执行的测试；支持目标与完成验收的平台分开列出。

## 构建与产物

- Windows 11 x64 原生构建；Ubuntu 20.04 x64 容器构建 Linux 产物。
- Python 3.10.19、PySide6 6.8.3、PyInstaller 6.22.2；使用仓库 uv.lock。
- 已生成 Windows 安装包、Linux .run、地面站便携 ZIP 和端侧七包 ZIP，均附 SHA-256 文件。
- FFmpeg 使用 release/ffmpeg.lock.json 固定归档与校验值；已验证 SRT 输入及 H.264 解码。
- 默认设备列表为空，包含完整设备类型图标、内置融合配置和示例；发布清单排除现场数据与开发环境。

## 已执行验证

| 环境或项目 | 结果与边界 |
| --- | --- |
| Windows 11（10.0.26200） | 静默安装至中文及空格目录、升级保留配置和数据、卸载保留数据、整目录搬迁通过。主机本身有开发工具，冻结程序测试使用隔离 PATH；不宣称等同全新系统验收。 |
| Ubuntu 20.04 x64 容器 | 普通用户、--network none、PATH 中无 Python/uv/FFmpeg：中文及空格路径安装、升级、卸载和重新安装通过；已有配置和数据保留。构建镜像中的 Python 位于普通用户不可访问的 /root 下。 |
| Ubuntu 22.04 x64 最小运行镜像 | 镜像未安装 Python、uv、FFmpeg；普通用户、断网完成同一安装生命周期和核心功能测试。 |
| 三个环境的冻结核心功能 | 日夜 SVG 与 Logo、VisPy 后端、Open3D 导入、MQTT Broker 启动、内置体素融合、NumPy 拼接、Open3D RANSAC/ICP 示例、worker 异常反馈、本地 SRT/H.264 解码通过。 |
| 主窗口启动 | 冻结程序及便携源码均完成真实应用初始化与退出；使用 Qt offscreen，测试夹具暂停设备网络启动。 |
| 便携 uv 路径 | 从压缩包解压，使用锁文件建立独立环境，实际执行 run.py 通过。 |
| 便携 pip 路径 | 独立虚拟环境安装 requirements.txt，实际执行 run.py 通过；修复旧版 pip 在中文 Windows 编码下读取依赖文件失败的问题。 |
| 路径与安装逻辑 | 源码/冻结路径、不依赖工作目录、写权限失败提示、主题本地持久化与迁移、安装哈希检查、失败回滚、升级/卸载/重装保留数据通过自动化测试。 |
| 针对性回归 | 43 项通过：发布工程、静态路径、SRT、ping 与版本测试。 |
| 完整回归 | 340 项中 338 项通过，2 项既有失败，见下文。 |

测试脚本：scripts/smoke_release.py、scripts/smoke_source.py、
scripts/prepare_linux_release_test.py、tests/test_linux_installer.sh、
tests/test_release_engineering.py。复现命令见 [发布指南](RELEASING.md)。

本次本地证据保存在 build/（不随发布包分发）：windows-release.log、
linux-release-resume.log、linux-offline-validation.log、linux22-offline-validation.log、
windows-final-smoke.json、installed-smoke-with-gui.json、relocated-smoke.json、
portable-uv-startup.log、portable-pip-startup.log、regression.log。
最终文件校验值以 dist/ 中同名 .sha256 为准。

## 既有回归失败

1. test_theme_v010.DayThemeTests.test_all_base_style_colors_are_translated_for_day_theme：
   17 个日间主题颜色未映射。使用 HEAD 中的原始 styles.py 复现同样失败。
2. test_wheeltec_r550p_profile 中的设备 profile 检查：UGV_003 的既有配置是
   scout_mini，测试期望 wheeltec_r550p；HEAD 中已是该配置。

这两项未因发布工程改造而引入；本次保留现场设备配置，不通过修改现场配置掩盖测试失败。

## 尚未完成的正式发布门禁

- Windows 10 实机；Windows 11 无开发工具的干净系统与断网完整验收。
- 四类目标系统的图形交互安装向导或终端交互安装，以及真实桌面启动、重启和卸载。
- X11/Wayland、不同显卡驱动与 OpenGL 实际绘制；offscreen 的 QOpenGLWidget 警告不计为渲染通过。
- 设备编辑、PCD/PGM 可视效果及实际设备联调的人工验收；worker 超时交互的完整端到端验收。
- Ubuntu 20.04/22.04 干净桌面下分别完成 uv 与 pip 便携部署；确认不选用系统 Python 3.8。

安装功能和本地构建入口已实现，以上人工与实机项目仍需执行后才能标记四平台正式验收通过。
本次未执行代码签名、公开发布或实际设备运动控制。
