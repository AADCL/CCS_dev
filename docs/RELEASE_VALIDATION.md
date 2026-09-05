# 发布验证记录

## v0.23.1 增量验证

日期：2026-09-05。产品补丁版本为 0.23.1，ROS 包版本保持不变，发布渠道为 pre-release-v0.23.1。

- 配置参考覆盖默认七份 YAML 和四套 profile 共 35 份文件的叶级参数；核对公开 launch 参数及 CCS_* 脚本环境变量。
- 检查当前文档链接/锚点、八包 README、产品版本和包版本，验证实际端侧/便携 ZIP 中的文档及相对链接。
- 增量运行版本、端侧布局/配置/视频配置、发布工程、设备配置、地图上下文和 Ground-Air profile 测试；命令为：
- 上述集合在 Windows / Python 3.10.19 发布环境中共 67 项，全部通过；日志保存在构建工作区 build/incremental-validation.log。

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
