# 开发笔记

## v0.13.1

### 已保存地图同步融合

- `MapFusionJob.sync_pgm` 将同步栅格作为离线点云融合的可选阶段。源 PGM 使用与 PCD 相同的主从关系，其中平移取 X/Y、旋转取 yaw；Z/roll/pitch 不参与二维栅格变换。
- 点云插件先生成并校验最终 PCD，`PgmFusionEngine` 再以该 PCD 的 XY 边界执行逆向最近邻融合，分辨率默认取来源最细值，冲突规则保持 `occupied > free > unknown`。
- `MapRepository.commit_fusion_result()` 在创建正式地图前验证 PCD 与 PGM；正式目录中的 `map.pcd`、`map.pgm`、`map.yaml` 和 schema 5 元数据作为一个提交单元写入，失败时移除新目录并保留 `.fusion/<job_id>`。

## v0.13.0

### PGM 文件传输

- `MapBuildingService` 继续独占 UDP 14562 socket，并把 PGM 扩展消息分发给 `PgmDownloadCoordinator`。下载协调器不创建第二个 socket，因此能严格保证实时建图与 PGM 下载互斥。
- 每个来源独立校验设备 IP、地图/设备/session 标识、manifest、CRC32、SHA-256、zlib 解压尺寸和 P2/P5 内容。分片可乱序并忽略重复，已接收分片与 `chunk-state.json` 落在 `.pgm_fusion/<job_id>`，重启后只请求缺片。

### 栅格融合与提交

- `PgmFusionEngine` 先按每个来源自己的 negate 和阈值分类，再从目标 PCD XY 栅格中心逆向映射到来源，使用最近邻采样避免旋转后的前向投影空洞。
- 合并优先级为 occupied、free、unknown；输出固定为 0/254/205、negate=0、origin yaw=0。分辨率不得细于来源最细值，输出范围固定为目标 PCD XY 边界。
- schema 5 的 `pgm_fusion` 保存目标 PCD SHA-256、来源、二维外参、产物哈希、分辨率和裁剪统计。提交前重新计算 PCD 指纹，并通过双文件备份和单次元数据写入替换 PGM/YAML。

## v0.12.0

### 配准插件样例

- NumPy RANSAC 插件先应用用户外参，再用分块最近邻、三点随机采样和 SVD 刚体估计修正残差；固定随机种子保证测试可重复，并通过体素采样限制计算规模。
- Open3D 插件采用 point-to-point ICP。两种插件均检查重叠质量，失败时抛出异常，由现有独立工作进程和输出校验机制阻止错误 PCD 提交。

### PCD 投影栅格

- `PcdToPgmGenerator` 在指定 Z 范围内把 XYZ 投影至 XY 栅格，PGM 文件按 ROS 图像方向上下翻转，使用 occupied=0、free=254、unknown=205 和 yaw=0。
- 未命中区域默认 unknown。地面站没有传感器射线观测，不能仅凭表面点云可靠推断 free；用户可为特定数据显式选择 free。
- 仓储在临时目录生成 PGM/YAML，再复用导入流程的双文件备份与原子替换。像素上限防止异常分辨率耗尽内存。

### 数字输入控件

- `NoButtonSpinBox` 和 `NoButtonDoubleSpinBox` 固定使用 `ButtonSymbols.NoButtons`，页面仍保留范围、精度、单位、键盘编辑和信号行为；QSS 同步隐藏原生子控件作为主题兜底。

## v0.11.1

### 共享地图拖动速度

- 三维地图页面均复用 `PointCloudViewer` 和 `MiddlePanTurntableCameraMixin`，因此拖动灵敏度必须在共享相机层调整，不能由地图页、任务页或指控大屏分别缩放。
- `MAP_PAN_DRAG_SPEED` 固定为 `3.0`。`calculate_turntable_pan()` 在视口归一化和地图 `scale_factor` 换算后应用该倍率，保持不同窗口尺寸及地图范围下的一致操作感。
- 相机距离不参与修改，右键拖动只移动观察中心，不会产生意外缩放。

## v0.11.0

### 融合插件与仓储

- `MapFusionRepository` 管理内置及导入算法，配置保存算法版本、脚本 SHA-256、启用状态、默认算法和 JSON 参数。导入脚本先通过 AST 检查，再复制到应用数据目录并在独立进程执行小型 PCD 验证。
- 插件工作进程只接收 JSON 请求和文件路径；返回结果后地面站重新使用 `MapPointCloudLoader` 校验有限 XYZ、点数和输出大小。进程隔离用于故障控制，不是恶意代码安全沙箱。
- 离线融合临时任务位于 `.fusion/<job_id>`，正式地图仅在插件输出校验成功后创建。

### 多设备建图

- `MapBuildingService` 维护一个 `_ActiveJob` 和多个 `_DeviceSession`。每台设备独立跟踪 session、来源 IP、序列、分片、体素、轨迹、重试和超时。
- 实时预览先把每台设备局部点云按 `primary <- secondary` 外参变换，再使用内置体素累计器合并；最终保存调用选定插件。
- schema 4 的 `build_provenance` 记录模式、来源、外参、算法 ID/版本/指纹、时间和剔除设备。旧 `last_mapping` 在单机建图中继续写入，供已有页面和调用兼容使用。
- 多设备开始需要全部 ACK。非主设备中断后进入降级态并等待用户剔除或中止；主设备中断不能继续提交。

## v0.10.1

### 原生 Qt 弹层主题

- 仅使用 QSS 时，`QComboBoxPrivateContainer`、对话框 `QListWidget` viewport 等原生子控件仍可能读取操作系统浅色 Palette。`build_qt_palette()` 现在同步 Window、Base、AlternateBase、Button、Highlight、Text 和 Disabled 角色，作为所有二级页面的底层兜底。
- `MainWindow` 初始化及日间/夜间切换时同时设置 QApplication Palette 与样式表，避免弹层在切换主题后保留旧背景。
- QSS 增加通用对话框列表/树/表格、菜单、组合框容器与 item 状态；任务、地图和大屏已有 objectName 专用规则继续后置覆盖。
- 夜间离屏检查覆盖类型模板、设备新建、状态卡编辑、地图新建、建图设备选择和任务新建对话框，并单独检查组合框弹层。

## v0.10.0

### 类型模板与迁移

- `DeviceTypeTemplateRepository` 独立持久化 schema 1 模板，ID 使用稳定大写标识。模板包含显示名称、应用内图标路径、`arrow/cube/sphere` 地图形状和默认状态卡片；配置损坏时进入只读状态且不覆盖原文件。
- `devices.json` schema 升至 3，并兼容读取 schema 1/2。旧版数组迁移为显式覆盖；schema 3 的 `null` 表示动态继承模板，空数组表示明确隐藏全部状态卡。
- 设备数据源负责把模板解析成 `DeviceSnapshot` 展示字段。模板更新只 replace 图标、名称、形状和卡片，不重建 MQTT 运行时字段。

### 图标与地图标记

- 上传图标先校验扩展名、5 MiB 上限和 Qt 可解码性，再复制到 `data/device_type_assets`。模板删除或替换后，未引用资产移入 `.trash`。
- `PointCloudViewer` 统一渲染设备标记：箭头按 UDP yaw 绕 Z 轴旋转，立方体使用 VisPy mesh，球体及渲染异常使用圆形 marker 回退。任务页和指控大屏只传 `DeviceMapMarker`，不读取模板文件。

### 日间主题

- 补齐 `BASE_STYLE` 中全部夜间硬编码颜色到 `ThemePalette` 的语义映射，覆盖设备选中态、地图按钮、任务编辑器 viewport/表格/输入/滚动条/splitter 及大屏按钮状态。
- 自动测试枚举 `BASE_STYLE` 颜色，防止新增夜间颜色未提供日间映射。离屏截图覆盖 800×600 和 1440×900；无 OpenGL 时验证回退页面与其余控件布局。

## v0.9.1

### 端侧任务协调

- 新增 `epgeneral_task_control` v0.1.0，严格接收 `ccs-task-control-v1`，按 task/subtask/revision 重组并校验 zlib JSON，再使用 ID 哈希目录原子保存单一 XML 事实来源。
- execution 状态机等待设备控制适配器的 scheduled feedback 后才确认 execute ACK；运行期间回传 1 Hz 心跳、状态和航点进度，超时或重启发布安全 STOP/CANCEL。
- 包定义强类型 ROS command/feedback 消息，不直接操作 MAVROS。Windows 测试验证协议、XML、状态机和 localhost UDP；真实 Melodic 设备控制适配器仍需目标设备联调。

### 任务编辑主题作用域

- `TaskEditorPage` 及设备列表、地图面板、航点表、冲突列表和审计列表使用独立 objectName，避免普通 Qt viewport、header 和 spinbox 绕过应用主题。
- `APP_STYLE` 在 `taskEditorPage` 作用域内统一面板、交替行、表头、选中/悬停状态、数值步进框和 splitter handle；样式不影响设备页、地图页和大屏现有组件。
- 任务列表空态继续使用独立深色 scroll viewport。修复仅影响绘制，不修改任务仓储、航点模型或 UDP 协议。

## v0.9.0

### 任务仓储与模型

- `TaskRepository` 将任务存放在 `data/task_server`，原子更新 `task.json`，追加写审计与执行 JSONL，并将删除目录移动到 `.trash`。执行开始时保存不可变快照，后续状态只更新执行记录。
- `TaskDefinition` 绑定地图 ID、frame 和地图指纹；`DeviceSubtask` 独立保存设备快照、航点、速度、启动延迟、修订与已下发修订。编辑后递增修订并立即使旧下发失效。
- 地图指纹变化时 UI 要求人工复核。设备记录被删除或 IP 缺失时保留历史任务，但执行服务拒绝下发。

### 选点与冲突

- `PointCloudViewer` 增加 browse/pick 模式及任务路径、冲突和执行标记接口。PCD 采用俯视视线与 `z=0` 平面求 XY；PGM 使用 origin/yaw、分辨率和图像 Y 翻转转换，只接受 free 栅格。
- `TaskConflictDetector` 从启动延迟、航段长度和巡航速度构造时间轴，在不同设备线性航段的重叠时间内求连续最近接近；水平、垂直和时间阈值均满足时生成稳定冲突记录。

### 下发与执行

- `TaskExecutionService` 独占 UDP 14564 后台 socket，向设备 14563 发送 prepare/chunk/commit/execute/cancel/stop。压缩任务通过 CRC32、修订和分片元数据校验，命令按 request ID 重试，支持端侧报告缺失分片后补发。
- 多设备执行先补齐未下发修订，再统一调度到当前 UTC 后 5 秒；设备锁阻止跨任务重复执行。ACK 拒绝、超时和任务心跳超时会归档失败并释放设备锁。
- Qt 页面只调用共享服务和仓储，不直接访问 socket。14564 绑定失败仅关闭下发/执行按钮，本地创建、编辑、冲突检查和历史查看继续可用。

### 页面联动

- `TaskPage` 使用列表/编辑二级栈；地图区可完全收起，UDP 全局位姿只更新执行标记，不生成模拟位置。
- 首页从真实执行快照计算次数和最近任务。指控大屏从任务仓储列出有效任务，自动选择关联地图，并复用同一执行服务启动或停止共同执行。

## v0.8.0

### 建图协议与线程

- `map_building_config.py` 将 UDP 端口、5 Hz 点云、0.10 m 体素、重试/超时和内存上限解析为冻结配置；错误只禁用地图详情的建图入口。
- `MapBuildingProtocol` 使用独立 `ccs-map-stream-v1` MessagePack 信封。解析层验证地图、设备、会话、消息类型、序列、包长、有限变换和点数，不复用 14560 遥测描述哈希。
- `MapBuildingService` 的后台线程独占绑定于 14562 的 socket，并使用同一 socket 向设备 14561 发送控制指令。线程内完成协议验证、分片重组和融合，通过 Qt signal 只传递不可变状态与预览数组。
- 开始/停止指令每 500 ms 重试，默认最多 5 次。分片 1 秒不完整则整帧丢弃；2 秒无完整帧进入 warning，5 秒中断。超时依据本地单调时钟。

### 点云融合与持久化

- `CloudFrameAssembler` 按 frame ID 接受乱序分片并忽略重复分片，拼接后先校验压缩帧 CRC32，再限制解压尺寸并解析 `<f4` XYZ。任何缺片、CRC、zlib、点数或非有限坐标错误都不会产生半帧。
- 端侧随每帧提供同步 `map <- body` 和 `body <- sensor` 四元数刚体变换；地面站矩阵相乘后一次性变换 NumPy 点数组，不对独立时间流插值。
- `VoxelMapAccumulator` 以整数体素键保存点坐标和计数，跨帧更新运行质心。预览使用稳定索引降采样，正式 PCD 保留所有融合体素。
- `MapRepository` 写 schema 3，兼容 schema 1/2。会话在 `.mapping/<session_id>` 原子写 `session.json`、`partial.pcd`、`trajectory.csv`；提交前使用现有 PCD loader 复验，再备份正式文件并替换。元数据失败时新结果退回临时目录，旧结果原位恢复。

### 页面生命周期

- `MappingDeviceDialog` 只读取 `creator_devices`，再与当前统一设备源合并 IP、MQTT 状态和 UDP 状态；离线不禁用，设备缺失或 IP 为空禁用。
- `PointCloudViewer` 使用独立 live markers visual，首帧前保留正式 PCD，建图中最多按配置频率刷新；完成后清除 live visual 并从仓储重新加载正式地图。
- `MapPage.set_active()` 与返回列表/窗口退出统一调用中断保存。再次进入详情时查询 `interrupted_sessions()`，用户可提交临时结果、丢弃或稍后处理。

### 验证边界

- Windows localhost 测试覆盖真实 UDP start/ACK/cloud/stop/ACK 和最终仓储提交。端侧 `epgeneral_map_stream` v0.1.0 已实现 `ccs-map-stream-v1` 并通过独立协议、处理、会话与 UDP 契约测试；真实 ROS Melodic 雷达/里程计联调仍需在目标设备执行。
- 新增端侧 `epgeneral_map_stream` v0.1.0；地面站版本仍为 v0.8.0，其余端侧 ROS 包版本不变。

## v0.7.1

### 紧凑趋势图

- `TelemetryChart` 由单一 `QChartView` 调整为带紧凑标题行的组合组件。标题、单位、60 秒窗口和三个彩色轴标位于同一行，Qt Charts 原生标题、图例和轴标题关闭。
- `QChart` 外边距和 graphics layout 边距归零，轴标签使用小字号；`QLineSeries`、动态 Y 范围、60 秒窗口和 1200 点缓存逻辑保持不变。

### 中键平移

- `PointCloudViewer` 使用 `MiddlePanTurntableCameraMixin` 覆盖未带修饰键的中键拖动。位移先按 viewbox 尺寸和相机 scale factor 归一化，再使用 TurntableCamera 当前 right/forward/up 轴转换到观察平面。
- 左键旋转、滚轮缩放及 Shift 组合行为继续交给 VisPy 基类。`calculate_turntable_pan()` 独立于 OpenGL 上下文，可用假相机验证中心变化和距离不变。

### 折叠状态

- `CollapsibleDevicePanel` 使用 `DevicePanelMode` 管理 summary/detail/collapsed，保留 `CollapsibleUavPanel` 兼容别名。设备源过滤只保留 MQTT ONLINE，不再限制设备类型。
- 完全收起时组件与水平 splitter 同步缩至约 36px，释放空间直接加入中央视图；恢复时返回先前模式和宽度。窄屏 `force_compact()` 只将详情变为摘要，不覆盖完全收起。
- `CollapsibleConsolePanel` 将标题状态栏和控制内容分离。收起时隐藏内容并将垂直 splitter 压缩至 36px，展开时恢复用户上一次高度；状态不写配置文件，仅在组件生命周期内保留。

## v0.7.0

### 大屏组件与生命周期

- `CommandDashboardPage` 复用统一 `DeviceDataSource`、`UdpTelemetryStore` 和 `MapRepository`，页面不直接连接 Paho、UDP socket 或文件系统。`devices_updated` 负责在线 UAV 列表，20 Hz UDP signal 只写趋势缓存和当前设备状态。
- `CollapsibleUavPanel` 默认显示名称、电量和状态，展开后补充 ID、任务、健康、飞行模式、解锁和心跳。`TelemetryStatusPanel` 将响应式收起与用户主动收起分离，避免窗口放大后状态栏永久隐藏或反复覆盖用户选择。
- 页面可见时启动 1 Hz 时钟、10 Hz 图表合并刷新和扫描线动画；隐藏时全部停止。`MainWindow` 集中管理导航显隐、`showFullScreen()`、Esc 和跨页面自动退出全屏。
- 上下 `QSplitter` 提供约 75%/25% 的监控区与控制台比例，水平 splitter 让三维视图优先获得额外空间；800×600 时右栏收起，宽屏按选择状态展开。

### 趋势与数字孪生

- `TelemetryTrendBuffer` 与 Qt Charts 解耦，按设备使用 `deque(maxlen=1200)` 保存单调时钟样本，并清理 60 秒窗口外数据。位置来自全局位姿；姿态优先全局位姿，缺失时回退 IMU，二者都缺失时不追加样本。
- Qt Charts 使用两个三轴 `QLineSeries` 图，UDP 仍按原始频率入缓存，页面以 10 Hz 批量替换图表点，防止高频 signal 触发过量重绘。切换设备只切换读取键，不清空历史。
- `PointCloudViewer` 增加 pointcloud/grid/overlay 图层状态、设备朝向轴和轨迹 visual。UDP 全局位姿直接按当前地图本地 ENU 坐标绘制，不在渲染层隐式执行坐标变换。

### PGM 仓储与渲染

- `MapRepository` 当前写入 schema 2，并兼容读取 schema 1。`PgmMapLoader` 解析 ROS YAML，严格校验 image、resolution、origin、negate 和阈值，再读取 P2/P5 灰度图并生成占据、空闲、未知三类 RGBA。
- PGM 与标准化 YAML 先写临时文件，再分别备份并替换活动图层；任一步或元数据提交失败都会删除本次文件并恢复旧文件。schema 2 读取时再次校验有限原点、正分辨率/尺寸和合法阈值。
- VisPy 将栅格按 resolution、origin 与 yaw 放置为地面平面；PCD 和 PGM visual 独立控制可见性。ZIP 导出枚举已持久化图层，不导出运行时轨迹和设备位置。
- OpenGL 初始化失败时保持标题、控制台和返回路径可用；自动化测试注入假 viewer 或使用离屏 Qt 验证状态，真实 GPU 像素与交互需在桌面 OpenGL 环境检查。

## v0.6.1

### 新建地图对话框修复

- `QScrollArea` 的 viewport 不会可靠继承外层深色背景；地图创建对话框为滚动区域、viewport 与内容容器设置了独立 object name，并在全局主题中显式指定背景、边框、复选框文字和悬停状态。
- `NewMapDialog` 将设备滚动区域保存为公开实例属性，UI 回归测试验证对象结构和已保存设备数量，离屏截图验证设备名称、ID、类型和在线状态可见。

## v0.6.0

### 地图仓储与持久化

- `MapRepository` 取代 `SystemOverview.maps` 作为地图运行时真值。仓储扫描 `data/map_server`，解析每个目录的 schema 1 `map.json`，并通过 `maps_updated` 同步地图页和首页计数。
- 新建地图先写元数据并进入 `waiting_for_pcd`；元数据采用同目录临时文件、`fsync` 和 `os.replace` 原子提交。显示名称与文件夹安全名称分离，稳定地图 ID 不随重命名变化。
- PCD 导入先复制到临时文件并完整解析，成功后原子替换 `map.pcd`，再保存点数及 XYZ 包围盒。解析失败不会破坏已有点云。
- 删除使用 `shutil.move` 移入 `.trash`；ZIP 下载也先写临时文件再替换目标，避免产生可见的半成品压缩包。
- 损坏目录被隔离成 `MapStatus.ERROR` 卡片，不阻止其他地图加载；错误地图禁止重命名、导入和下载，但允许移入回收目录。

### PCD 与三维显示

- `MapPointCloudLoader` 使用 pypcd4 读取 ASCII、binary 和 binary-compressed PCD，并保留一个 ASCII 后备解析器，便于在缺少可选解析库时给出明确错误。
- 点云加载后先检查 XYZ 维度、空数据和有限数值，再计算包围盒。超过显示上限时使用固定索引等距抽样，只缩减传给 GPU 的数组。
- `PointCloudViewer` 将 VisPy `SceneCanvas` 嵌入 PySide6，使用 TurntableCamera 提供旋转、缩放和平移；点云、坐标轴和未来设备标记使用独立 visual。
- VisPy 在组件初始化时延迟导入。缺少依赖或 OpenGL 上下文时只展示错误面板，主窗口、MQTT、UDP 和 RTSP 生命周期不受影响。
- `set_device_markers()` 接受独立 `DeviceMapMarker` 集合，本版本传入空集合，为后续 UDP 位置接入保留稳定边界。

### 地图页面

- 地图选项卡内部使用 `QStackedWidget` 切换卡片列表与三维详情。列表按宽度重排为 1、2、3 列，名称搜索不触碰仓储数据。
- 新建对话框直接使用统一设备快照生成多选列表，不按在线状态过滤；元数据保存创建时的设备名称、ID 和类型快照。
- 编辑模式集中维护地图 ID 集合，单选时启用重命名、PCD 导入和下载，多选仍可批量移入回收目录；双击语义在编辑模式下禁用。
- 首页地图数量改为订阅仓储信号，不再读取 v0.0.2 的三张模拟地图。

## v0.5.1

### catkin Python 导入修复

- 报错进程路径位于工作空间 `src/epgeneral_udp_telemetry/scripts`，而不是 catkin 生成的 `devel/lib/epgeneral_udp_telemetry`，说明 roslaunch 使用了源码脚本且当前 Python 路径没有已构建包。
- 启动脚本在检测到相邻 `../src/epgeneral_udp_telemetry` 时，将该目录加入 `sys.path`；安装后的 catkin relay 不存在该相邻结构，因此仍使用 devel/install 的标准 Python 路径。
- CMake 在 `find_package(PythonInterp 3.6)` 后将解释器写回 cache，防止 Melodic 的 Python 2 缓存与 `catkin_python_setup` 生成路径不一致。
- 新增独立子进程测试，清除包源码 PYTHONPATH 后直接加载入口脚本，验证其能够自行定位模块；CMake 同时通过 `catkin_add_nosetests` 注册包内测试。

## v0.5.0

### 设备级状态卡配置

- `DeviceProfile` 和 `DeviceSnapshot` 增加末尾字段 `status_card_ids`，默认使用六项固定目录，保持旧位置参数调用兼容。
- `devices.json` schema 升至 2；仓储接受 schema 1 并在成功解析后原子迁移。未知、重复或非数组卡片配置会进入原有配置错误保护。
- `update_status_cards` 只替换目标设备档案和快照中的卡片集合，不重新合并 MQTT 运行态，因此编辑卡片不会清空电量、任务或连接状态。
- 新建设备自动获得默认六项；允许保存空集合，详情页以明确空态代替无意义的占位卡。

### 状态卡界面

- 数据接收区使用同层级 `DataStatusCard` 网格，每张卡包含状态点、短标题、主值和辅助信息，避免在一个大卡片内部堆叠状态。
- 卡片根据详情页宽度重排为 1、2、3 列。实时更新只修改已有 QLabel，不重建卡片；只有设备绑定集合变化时才重建网格。
- `StatusCardEditorDialog` 按固定目录提供复选框、全选、清空和保存。详情页只发出选择结果，持久化和错误提示仍由 `DevicesPage`/数据源负责。
- Livox 卡将一级可用性与二级点云频率组合展示；其余生成器卡按三级话题新鲜度显示，建图模式卡显示文本值并以新鲜度控制颜色。

### UDP 与端侧适配

- UDP 描述新增 `text_status` 类型，严格限制文本不超过 128 字符，并同时携带 available/unavailable/unknown、新鲜度和最近值。
- 端侧 `TelemetrySampler` 对文本状态取窗口内最后值，低频时复用最近值；`node.py` 通过可配置点分路径读取 `std_msgs/String` 或同类 ROS 消息。
- 默认三级状态话题扩展为 Livox、FAST-LIO2、PGM、OctoMap、OccupancyGrid 和 mapping mode，两端描述哈希测试保证配置同步。

## v0.4.0

### UDP 协议与线程边界

- `udp_config.py` 校验监听地址、心跳阈值和遥测描述，并将两端公共描述按名称排序后生成 SHA-256；topic 等端侧私有字段不参与哈希。
- `udp_protocol.py` 严格解析版本化 MessagePack 信封，校验协议、描述哈希、设备、会话、消息类型、等级、序列、有限数值和 16 KiB 包长。
- `UdpReceiverThread` 使用阻塞 UDP socket 和短超时在独立 `QThread` 中接收，跨线程只发出 bytes/peer signal；`UdpTelemetryStore` 在主线程维护不可变快照。
- 会话 ID 变化时清理该设备旧序列；同会话、同消息类型和等级的重复或倒退序列被拒收。心跳超时只使用本地 `time.monotonic`。

### 高频数据与界面

- `DeviceTelemetrySnapshot` 与 MQTT 的 `DeviceSnapshot` 分离，避免 20 Hz 遥测触发首页、地图和设备卡片重绘。
- 详情页保存最新 UDP 快照，并以 50 ms QTimer 合并绘制位姿和 IMU；页面隐藏后停止绘制定时器，后台接收与状态仓储继续运行。
- 点云模型只包含可用性、估算频率和数据年龄。三级状态使用 available/unavailable/unknown，对应绿、红、灰指示。
- UDP 心跳 warning/error/recovery 通过公开日志入口合并到已有设备环形日志；MQTT 在线状态和 UDP 链路状态互不覆盖。

### 端侧 ROS 采集

- `epgeneral_udp_telemetry` 面向 ROS Melodic/Python 3.6.9，利用 `roslib.message.get_message_class` 动态加载 Pose/IMU 类型，状态监测使用 `rospy.AnyMsg`，因此可同时服务 MAVROS 和普通 ROS 设备。
- 一级采样窗口对位置、角速度和加速度求均值；四元数先按参考四元数调整符号，再归一化平均并转换为 roll/pitch/yaw。
- 各等级由独立 ROS Timer 固定以 20/5/1 Hz聚合发送。窗口无新样本时复用最近输出并更新 sample age；点云仅由回调时间估算接收频率。
- 地面站 JSON 与端侧 YAML 分别部署，根测试读取两端配置并验证描述哈希完全一致。

### 验证边界

- Windows 可验证配置、协议、平滑、状态机、UDP localhost 和 Qt 离屏界面；ROS 动态订阅、catkin 构建和真实 MAVROS 话题需在 Ubuntu 18.04 + ROS Melodic 环境执行。

## v0.3.0

### 地面站 RTSP 详情页

- `rtsp_video.py` 集中处理 IPv4/IPv6 URL 生成、`QMediaPlayer` 生命周期和播放状态，固定输出 `:8554/usb_cam`。
- `RtspVideoWidget` 通过 `QVideoWidget` 显示视频，开关默认关闭；设备切换、详情页隐藏、主导航切换和窗口关闭都会停止媒体。
- 详情页使用 `QGridLayout` 在宽屏左右分栏，低于约 1000px 时自动切换上下结构；信息字段和视频面板均保留最小尺寸。
- 播放器工厂可注入假对象，Qt 离屏测试不依赖真实 RTSP 网络；播放错误只影响视频组件，不修改设备快照。

### 端侧目录与共享配置

- `edge_side_pkg` 是端侧部署容器，当前包含 `epgeneral_device_config`、`mqtav`、`epgeneral_usb_cam_rtsp`、`epgeneral_udp_telemetry` 和 `epgeneral_map_stream` 五个 ROS 包。
- 2026-08-18 新增独立 `EPGeneral_multi_map` 目录（ROS 包名 `epgeneral_multi_map`）v0.1.0。它面向 Ubuntu 20.04/ROS1 Noetic/Python 3.8+，按地面站统一绝对时间完成点云/位姿插值配准和切片上传；端侧不融合、不落盘、不新增滤波，并与 `epgeneral_map_stream` 单机互斥。
- 新包自动化已覆盖严格联合命令、窗口/尾片/超时/复位、Python 3.8 语法、真实 localhost UDP 单节点链路和当前 `CloudFrameAssembler` 重组。当前地面站 v0.13.1 还缺严格联合下行字段，且 Windows 环境不能执行 Noetic Catkin 或双机器人实机验收；详见 `docs/地面站兼容扩展说明.md`。
- `epgeneral_device_config/config/device.yaml` 是设备 ID/IP 的唯一端侧来源，根测试与 `config/devices.json` 做一致性校验。
- mqtav `v0.3.0` 将设备身份加载与 MQTT/ROS 运行配置拆开，新增 `--device-config-file`；保留 Python 3.6.9 与 ROS Melodic 兼容实现。

### USB 摄像头 RTSP 推流

- `epgeneral_usb_cam_rtsp` 使用 C++、`image_transport`、`cv_bridge` 和 GStreamer RTSP Server，订阅 `/usb_cam/image_raw`。
- ROS 回调只负责把最新 BGR8 帧复制到受 mutex 保护的单帧缓存；GStreamer `appsrc` 在 RTSP 客户端请求时取最新帧，队列限制为 2 帧并丢弃旧帧，避免慢客户端阻塞相机。
- 编码管线为 `videoconvert -> x264enc(tune=zerolatency) -> rtph264pay`，默认 `640x480/30 FPS/2000 kbps`，服务 `0.0.0.0:8554/usb_cam`。
- WallTimer 检查首次收帧和断帧超时，RTSP 客户端连接/断开、编码错误和关闭均写 ROS 日志。

### 验证边界

- Windows 工作区验证地面站 Python/Qt 测试、RTSP URL 和播放器生命周期；ROS Melodic/GStreamer 编译及真实摄像头验证需在 Ubuntu 18.04 目标机执行。

## v0.2.0

### 运行架构与依赖

- 地面站版本从 `0.1.0` 升至 `0.2.0`，mqtav 协议未变，机载 ROS1 包保持 `0.1.0`。
- `MqttBrokerService` 使用 `amqtt`，在 daemon asyncio 线程中启动 TCP Broker；匿名认证通过 `AnonymousAuthPlugin` 显式配置。
- `MqttSubscriber` 使用 Paho v2 callback API 和网络线程。连接成功后订阅三个通配主题，回调只复制 topic/payload 并发出 Qt signal。
- `MqttMonitoringRuntime` 负责生命周期编排：Broker 成功后才启动订阅器，退出时先停止 Paho，再通知 Broker 事件循环并等待线程结束。跨线程停止标记覆盖事件循环尚未创建的立即退出竞态；绑定失败只更新模块故障状态，不结束 QApplication。

### 消息边界

- `MqttMessageParser` 将 UTF-8 JSON 转换为冻结的 presence、heartbeat、status 事件，不向 UI 暴露原始字典。
- 解析器验证三段主题、schema `1.0`、message type、带时区时间戳、payload/topic 设备 ID、非负 sequence、布尔/数值/字符串类型和 0–100 电量范围。
- `MqttDeviceSource` 再校验设备是否存在、静态 IP 是否一致和同设备同消息类型 sequence 是否递增。格式或未知设备错误写入标准日志并发出 `protocol_warning`；设备相关异常进入该设备环形日志。
- 心跳超时使用注入式 `time.monotonic`，测试无需等待真实秒数，机载时间偏差不会影响在线判定。

### 状态机与数据映射

- MQTT 模式从 config 档案创建离线/未知快照，不继承模拟电量、任务、坐标或连接状态。
- heartbeat 立即转在线并更新最后心跳；超过 2 秒首次转 warning，超过 5 秒首次转 offline。tracker 中的 `warned`、`errored` 防止每次 1 Hz 检查重复写日志。
- retained offline presence 从收到时开始断联计时并立即 warning；online presence 只记录 Broker 连接事件。
- FCU 连接映射为健康/需关注，未上报为未知。飞行模式保留 MAVROS 原值，定位状态仍是独立字段。
- status 保存电量百分比、电压、电流、armed、system status、原始任务值，并将常见任务别名映射到 `TaskStatus`。
- 日志采用 `deque(maxlen=500)`，heartbeat/status 为 info，首次断联为 warning，5 秒断联为 error；详情页原有等级筛选无需了解 MQTT。

### UI 解耦与测试

- 首页、设备和地图页面的构造参数改为 `DeviceDataSource` 协议。页面只订阅 `devices_updated`，Paho 和 Broker 线程不直接访问控件。
- 设备卡增加飞行模式；详情页增加最后心跳、飞行模式、解锁、MAVLink system status、电池电压/电流和原始任务值，并支持未知健康状态。
- 设备页监听 `module_status_changed`，区分订阅正常和 Broker/订阅故障样式。
- 测试使用真实临时端口启动 Broker 并由 Paho 发布 mqtav 信封，同时覆盖端口占用、干净关闭、可控时钟状态机、乱序、未知设备、日志上限和 Qt 页面字段联动。

## v0.1.0

### 版本与模块

- 本次为向后兼容的重要设备管理能力扩展，语义化版本从 `0.0.2` 升级到 `0.1.0`。
- `device_config.py` 使用标准库 `json` 读取配置，使用同目录临时文件、`fsync` 和 `os.replace` 原子保存，避免写入中断产生半文件。
- `device_dialogs.py` 管理设备输入、重复 ID 和 IP 校验，以及异步连通性测试。
- 设备选项卡内部使用第二层 `QStackedWidget` 切换设备列表和详情页，主导航结构保持不变。

### 静态配置与运行时状态

- `DeviceProfile` 只描述名称、类型、ID、IP、最近可用状态和测试时间，由 `config/devices.json` 持久化。
- `DeviceSnapshot` 是页面消费的完整快照。数据源按设备 ID 将档案与模拟电量、连接、任务、定位和地图坐标合并。
- 配置缺失时初始化六台示例设备；解析失败时仓储进入只读状态并保留损坏原文件，界面禁用所有写操作。
- 创建和删除均先成功写入配置，再更新内存快照并发送 `devices_updated`，使首页、设备页和地图页保持一致。

### 连通性测试

- IP 使用 `ipaddress.ip_address` 严格校验，只接受 IPv4/IPv6，不接受主机名。
- ping 命令以参数数组执行且固定 `shell=False`。Windows 使用一次请求与 1500 ms 等待参数，类 Unix 系统使用一次请求与 2 秒等待参数。
- `PingWorker` 基于 `QRunnable` 和全局 `QThreadPool`，结果通过 signal 返回 UI 线程，测试期间不阻塞窗口。
- IP 改动会使先前测试结果失效；测试成功和失败均可创建，分别记录为可用和不可用，名称或 ID 的合法修改无需重复 ping。

### 编辑、详情与日志

- 编辑模式由设备页统一维护选中 ID 集合，卡片只负责显示复选框并发送选择变化；退出模式会清空选择。
- 删除前使用确认框保护，确认后调用配置仓储永久删除。测试使用临时配置目录，不接触项目真实 config。
- 设备详情页展示静态档案和运行时快照的全部字段，并按窗口宽度重排为 3、2、1 列。
- 健康状态由连接、定位和电量计算：离线或定位丢失为异常，告警或电量低于 25% 为需关注，其余为正常。
- `DeviceLogEntry` 包含时间、等级和消息；当前数据源生成内存日志，详情页通过等级下拉框过滤，接口可由后续 ROS 日志源替换。

### 测试思路

- 配置测试覆盖初始化、原子持久化、重复 ID、非法 IP、删除和损坏文件只读保护。
- ping 测试注入进程执行器，验证平台命令、成功、失败和超时，不依赖实际网络。
- Qt 离屏测试覆盖编辑复选框、确认删除、对话框状态失效、详情导航和日志筛选。

## v0.0.2

### 技术栈与模块

- 使用 Python 3.10+ 和 PySide6 6.6+ 构建桌面界面。
- `main_window.py` 作为应用外壳，使用 `QButtonGroup` 管理互斥导航按钮，使用 `QStackedWidget` 切换五个功能页面。
- `pages/` 目录将首页、设备页、地图页和占位页隔离，避免主窗口承担具体业务布局。
- `version.py` 提供唯一的 `__version__`，由窗口标题、首页和 `QApplication` 元数据共同读取。

### 数据流

- `DeviceSnapshot` 是设备界面的稳定输入模型。v0.0.2 在原字段之后增加 `position_x`、`position_y` 和 `frame_id`，保持旧位置参数调用兼容。
- `SimulatedDeviceSource` 通过 Qt `Signal` 发布设备列表。首页、设备页和地图页订阅同一信号，因此一次刷新可以同步更新三个页面。
- 首页在线数直接由设备快照计算；地图数量、任务次数和上次任务来自 `SystemOverview` 模拟概览。
- ROS 适配器只负责将外部消息转换为 `DeviceSnapshot`。位置字段不完整时忽略位置，不阻塞设备基础信息显示。

### 响应式布局

- 主窗口最低尺寸为 800×600，页面内部使用布局管理器和滚动区域控制溢出。
- 首页根据可用宽度将四个统计卡重排为 4、2、1 列，任务字段重排为 2、1 列。
- 设备页在窄窗口中把筛选控件分为多行；设备卡片按视口宽度切换 3、2、1 列。
- 地图页通过 `QSplitter` 分配左右列表与中央画布，中央画布设置为主要伸缩区域。
- `resizeEvent` 只负责重新放置已有控件，不创建新的业务对象或改变数据状态。
- 动态重排设备卡片时先隐藏旧控件、再使用 `deleteLater()` 延迟销毁，并在事件循环空闲阶段完成列数切换，避免 Qt 绘制期间访问已移除控件。

### 地图实现

- `MapCanvas` 继承 `QGraphicsView`，每张地图使用米制尺寸建立 `QGraphicsScene`。
- 画布以 10 像素/米绘制 5 米间隔网格，并标出局部坐标轴。
- 设备点位按 `position_x`、`position_y` 投影；Qt 场景 Y 轴向下，因此绘制时对 Y 坐标取反。
- 设备颜色对应在线、需关注和离线状态；文字标签忽略视图缩放，保证放大或缩小时仍可读。
- `ScrollHandDrag` 提供拖拽平移，滚轮事件通过受限缩放级别控制画布比例。
- Windows 打包运行时若未自动发现中文字体，应用会显式注册系统微软雅黑或黑体；其他平台优先使用系统已注册的中文字体。

### 测试思路

- 数据层测试不导入 PySide6 页面，能够在缺少 GUI 依赖时验证模型、版本号和 ROS 转换器。
- UI 测试设置 `QT_QPA_PLATFORM=offscreen`，验证导航、首页统计联动、设备卡片重排和地图切换。
- UI 测试在 PySide6 不可用时明确跳过，安装依赖后自动纳入完整测试集。

## v0.0.1

### 技术栈与模块

- 使用 PySide6 创建单窗口设备链接与显示页面。
- `DeviceSnapshot` 使用冻结数据类描述设备 ID、名称、类型、电量、定位、任务、连接和更新时间。
- `SimulatedDeviceSource` 提供六台模拟设备，并通过 Qt signal/slot 刷新界面。
- `ros_adapters.py` 为 ROS1 和 ROS2 提供不依赖具体消息包的转换边界。

### 界面方法

- 设备以 `DeviceCard` 卡片展示，使用 Qt 样式表表达连接状态、低电量和选中状态。
- 搜索、设备类型和连接状态可以组合过滤。
- 卡片网格根据视口宽度在 1–3 列之间切换，但标题统计和工具栏仍包含固定尺寸，尚未形成完整响应式页面。
