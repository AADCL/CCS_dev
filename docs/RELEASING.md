# 发布与安装指南 · v0.23.1

本次渠道为 `pre-release-v0.23.1`。地面站安装包与便携包含 `docs/edge/` 离线端侧文档；端侧 ZIP 保留 `edge_side_pkg/` 布局。仅源码链接需要联网访问该标签，完整操作与参数文档可离线阅读。

## 发布形式

| 文件 | 内容 |
| --- | --- |
| CCS-0.23.1-windows-x64-setup.exe | Windows 10/11 x64 离线安装包 |
| CCS-0.23.1-linux-x64.run | Ubuntu 20.04/22.04 x64 离线安装包 |
| CCS-0.23.1-portable.zip | 地面站源码、锁文件、干净配置及依赖安装脚本 |
| CCS-0.23.1-edge.zip | 七个公共 ROS 包、Ground-Air 控制包、部署 profile 与协议说明 |

每份产物附带同名 .sha256 校验文件。包内 release-manifest.json 记录版本、
平台、构建解释器、文件列表和校验值。安装包不要求目标机安装 Python、uv 或
FFmpeg；便携版需要在首次启动前准备依赖。

支持矩阵是发布目标。实际验证结果见 [发布验证记录](RELEASE_VALIDATION.md)，
不能把构建成功视为四系统全部完成实机验收。

## 安装包使用

Windows 双击安装包，选择当前用户可写的目录。默认位置为
%LOCALAPPDATA%\Programs\CCS，可选择创建桌面快捷方式。

静默安装示例：

    CCS-0.23.1-windows-x64-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="D:\Applications\CCS"

Ubuntu：

    bash CCS-0.23.1-linux-x64.run
    bash CCS-0.23.1-linux-x64.run --prefix "$HOME/Applications/CCS" --yes
    "$HOME/Applications/CCS/CCS"

Ubuntu 必须已有桌面环境与兼容显卡驱动。基线为 x86_64、glibc 2.31（Ubuntu
20.04）、OpenGL 2.1+。图形运行库由构建过程收集；驱动、X11/XWayland、
系统 glibc 不作为应用私有库替换。若使用精简桌面镜像，可由管理员预先安装：

    sudo apt install libgl1 libegl1 libglib2.0-0 libxkbcommon-x11-0 libxcb-cursor0 fonts-noto-cjk

上述系统准备步骤需要系统包源；在已准备好的桌面系统上，CCS 安装与启动不联网。
NTP UDP 123 的绑定权限、已有时间服务占用和防火墙配置见
[使用与部署指南](USER_GUIDE.md)。安装器不会停用系统服务或修改防火墙。

## 便携版准备与启动

解压后在软件根目录执行。Ubuntu 20.04 自带的 Python 3.8 不满足要求；
uv 按 .python-version 选择 Python 3.10.19，pip 路径需自行提供 Python 3.10–3.13。

推荐方式：

    uv sync --locked
    uv run --no-sync python run.py

原有 uv run python run.py 命令仍可使用。需要保证启动时不联网时使用 --no-sync。

也可运行 scripts/setup_env.ps1 或 bash scripts/setup_env.sh。
它们只安装依赖，不自动启动应用。备用 pip 方式：

Windows：

    powershell -File scripts/setup_env.ps1 -Method pip -Python python
    .venv\Scripts\python.exe run.py

Ubuntu：

    bash scripts/setup_env.sh pip python3.10
    .venv/bin/python run.py

激活 .venv 后可直接 python run.py。便携版的视频功能还需系统 FFmpeg，
ffmpeg -hide_banner -protocols 的 Input 部分必须含 srt。

## 数据目录、升级与卸载

所有持久化业务文件位于软件根目录：

| 目录 | 用途 |
| --- | --- |
| config/ | 设备、连接配置、算法注册表、appearance.ini 主题 |
| data/ | 地图、任务、类型图标、导入算法、历史及日志 |
| data/logs/ | 轮转应用日志，单文件 5 MiB，保留 3 份备份 |
| _internal/ | 冻结安装包的内置模块、Qt 和共享库 |
| tools/ffmpeg/ | 随安装包分发的视频运行组件 |

请使用当前用户可写的专用目录；启动时会实际检查写权限。目录不可写会显示错误，
不会改存至其他用户目录。业务文件中的受管资源继续使用相对路径。
系统临时目录只保存可清理的中间文件。

升级前关闭 CCS，备份 config/ 和 data/，再安装到原位置。程序和内置资源更新，
现有配置和数据保留；新默认文件仅在缺失时补齐。Linux 在覆盖失败时恢复原程序文件。
外部算法使用安装包内已有依赖；需要额外 Python 包的自定义插件应使用便携版环境。

Windows 通过系统“已安装的应用”卸载。Ubuntu：

    "$HOME/Applications/CCS/uninstall.sh"
    "$HOME/Applications/CCS/uninstall.sh" --yes

两者默认保留 config/ 和 data/。Linux 同时保留最小安装标记，以便重新安装时识别原数据目录。完成备份后才手动删除这些目录。
搬迁须关闭程序并复制整个软件目录；旧快捷方式需要重新创建。Windows 安装版移动后应重新运行安装器并指定新目录，以更新卸载登记；原有配置和数据会保留。
首次源码启动会迁移旧系统 QSettings 中的主题，安装包初始使用独立本地主题。

## 本地构建

构建须使用完整源码仓库；便携包不包含构建工具。构建机可联网，安装目标机可离线。安装 uv 后：

Windows（另需 Inno Setup 6，ISCC.exe 可加入 PATH）：

    powershell -File scripts/build_release.ps1 -Target all

Ubuntu 20.04（准备运行库与 binutils）：

    bash scripts/build_release.sh all

目标可选 installer、portable、edge、all。统一入口：

    uv run --group release python scripts/build_release.py --target portable
    uv run --group release python scripts/build_release.py --target edge

安装包必须由 Python 3.10 在本平台构建，工具组 release 固定 PyInstaller 版本。
PySide6 固定 6.8.3，避免较新版 Linux wheel 的 glibc 要求超过 Ubuntu 20.04。
构建脚本不读取现场 config/ 或 data/，只使用 release/defaults/ 中的发布模板；
设备列表为空，类型图标齐全，仅默认注册内置融合算法，examples/ 提供外部算法示例。

Windows 构建器自动寻找 PATH、常见 Inno Setup 6 安装目录和
build/tools/InnoSetup/ISCC.exe；也可传入 --iscc。
FFmpeg 首次构建时下载到 build/downloads，固定来源和 SHA-256 见
release/ffmpeg.lock.json。离线构建可传 --ffmpeg-archive，仍须满足该校验值；
不允许无校验地改用本机任意 FFmpeg。

Inno Setup 官方下载：https://jrsoftware.org/isdl.php
FFmpeg 来源：https://www.gyan.dev/ffmpeg/builds/ 与 https://github.com/BtbN/FFmpeg-Builds

Linux 容器构建（Docker 必须运行 Linux 容器引擎）：

    docker build -f release/linux.Dockerfile -t ccs-release:20.04 .
    docker run --rm -v "/absolute/CCS_dev:/source:ro" -v "/absolute/CCS_dev/dist:/output" ccs-release:20.04

Windows 的 -v 路径可使用 D:/Projects/...。先创建 dist 目录。
容器不会使用主机的 .venv，也不会把主机现场数据放入产物。
原生 Ubuntu 22.04 不作为最低兼容构建环境，请使用 Ubuntu 20.04 容器。

所有产物输出至 dist/；build/release-* 保留构建日志和冻结目录供诊断。
未配置 CI 和代码签名。本次按增量验证发布预发布版本；四系统完整实机门禁保持单独记录，不能将预发布标记为正式验收通过。

## 发布门禁

- 新安装、指定目录、静默安装、离线启动、升级、卸载保留数据。
- 中文/空格目录、不同工作目录、整目录移动、不可写目录错误提示。
- 图标、日夜主题、PCD/PGM、内置与 NumPy/Open3D 融合及 worker 故障。
- MQTT Broker 动态插件、SRT 协议和本地视频解码；无设备时其他页面可用。
- uv 和 pip 便携部署，产物清单与数据排除检查。
- Windows 10/11、Ubuntu 20.04/22.04 分别记录系统、显卡/显示环境、
  产物校验值、测试结果及未验证项；真实设备运动联调不由打包测试替代。

## 可复用验证

冻结目录验证：

    python scripts/smoke_release.py /path/to/frozen/CCS --output build/smoke.json

此流程验证主窗口初始化、图标、MQTT、四类融合与 localhost SRT。GUI 使用 offscreen，不替代真实显卡验收。

Ubuntu 容器验收先执行 python scripts/prepare_linux_release_test.py，再使用 release/linux-runtime.Dockerfile 构建运行镜像；以普通用户、--network none 运行，挂载 dist 到 /release、build/linux-validation 到 /validation。入口 tests/test_linux_installer.sh 验证安装、升级、功能、卸载和重新安装。

Docker Hub 不可达时，可用 --build-arg UBUNTU_IMAGE=public.ecr.aws/ubuntu/ubuntu:20.04（22.04 验证镜像对应使用 22.04）。容器下载域名不可达时，可在主机获取 ffmpeg.lock.json 中的归档，核对 SHA-256 后通过只读挂载及 --ffmpeg-archive 提供。
