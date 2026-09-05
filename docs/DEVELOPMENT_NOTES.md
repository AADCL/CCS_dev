# 开发笔记

## v0.23.1 端侧文档与分发

- 端侧 README、INTERFACE_REFERENCE 和 USER_MANUAL 分别负责总览、真实接口/配置契约和操作流程；包级及设备专项文档链接到统一入口。
- profile 配置与单包默认配置是两种启动入口，不能写成通用 rosparam 覆盖；当前没有热重载。
- 发布时将端侧 Markdown 分发到地面站 docs/edge，并按产物布局重定位链接；未分发源码链接到 pre-release-v0.23.1。
- 验证范围限定为版本、文档覆盖/链接、配置、受影响设备修复和发布内容，历史验收记录保留原日期与结论。

## v0.23.0 地图界面与电池估算

- `MapMarkerShape` 支持 `arrow/cube/sphere/origin`。`cube` 是兼容配置值，界面名称为“长方体”；VisPy `create_box()` 返回 `(structured_vertices, triangle_faces, outline_edges)`，必须使用 `structured_vertices["position"]` 和三角面，不能按 `MeshData` 调用。长方体与球体使用 opaque + depth test，箭头和 9 px 原点使用顶层半透明绘制。
- 网格坐标不再单独持久化开关或刻度间距。坐标只随网格显示，刻度必须是网格分辨率的 1/2/5/10 整洁倍数；网格关闭时分辨率和透明度控件一起禁用。
- 指控大屏不得重新引入扫描覆盖层或动画计时器。顶栏为左侧 MQTT/UDP 胶囊、中间中英文标题和 72 px 强调线、右侧在线数量/时钟胶囊；右栏展开时用向右图标表示向窗口边缘收起，收起时用向左图标表示向地图展开。
- `DeviceProfile.battery_profile` 与重定位 profile 独立。设备配置 schema 7 对 schema 1–6 自动迁移；缺少字段时仅对同名 Scout/WheelTech 重定位 profile 推断。MQTT 合法原始百分比优先，缺失时才采用电压估算；统一快照低于 25% 时标记为需关注。
- 电池配置 schema 2 支持多个 profile，各设备/profile 保留 15 样本滚动中位数，分钟历史保留两天。曲线、异常值和现场校准规则见 [设备电池曲线校准](BATTERY_CALIBRATION.md)。Scout 初始曲线属于待实测基线，不得写成厂商 SOC 数据。
- 本次保持 v0.23.0、端侧包版本和 MQTT wire schema 不变。Qt offscreen 可验证控件和几何数据，但遮挡、球面着色和日夜顶栏仍需在实际 OpenGL 桌面人工检查。

## v0.23.0 发布工程

统一路径接口位于 ccs_monitor.runtime_paths：application_root 管理可写配置和数据，resource_root 管理冻结资源。禁止新增基于 CWD 或 _internal 的持久化目录。主题默认使用 config/appearance.ini；首次源码启动读取旧 QSettings。融合安装版调用独立 worker，FFmpeg 经外部进程环境适配后启动。

release/defaults 是发布配置唯一来源，不从现场 config/data 生成安装包。维护默认设备类型时须同时维护其图标。构建入口、依赖基线、清单、升级卸载约束和实际验证状态见 [发布指南](RELEASING.md) 与 [验证记录](RELEASE_VALIDATION.md)。



## v0.22.9 首页、设备页图标与 AADCL 品牌

- 所有应用 SVG（含无主题的 `devices_online.svg`、`devices_offline.svg`、`devices_warning.svg`）统一由 `ccs_monitor.app_icons` 从模块相对路径 `icons/app_icons/` 解析，不依赖运行目录。共享 `ccs_monitor.widgets.CardIcon` 提供固定尺寸、Tooltip、可访问名称与主题刷新；缺失或不可渲染时记录告警并隐藏图形，卡片文字/数据不受影响。
- 首页全览：在线/离线分别使用上述静态设备图标，本地地图数量使用 `mapStorage`，任务执行次数使用 `tasks`，图标为 28 px。
- 首页子系统（24 px）：NTP→`time`、MQTT Broker→`mqttbroker`、MQTT 数据订阅→`mqtt`、UDP 高频遥测→`UDP`、FFmpeg/SRT→`camera`、实时建图→`realTimeMapping`、UDP 任务控制→`UDPtask`、设备重定位→`localization`、地图仓储→`mapStorage`、任务仓储→`taskStorage`。
- 主题文件后缀均为 `_day.svg` / `_night.svg`，大小写必须准确；旧 `mapstorage` 通过临时文件名完成 Git 大小写重命名，两处地图卡共用新设计。`taskStrorage` 仅为输入素材拼写，仓库规范名为 `taskStorage`。camera、mqtt、mqttbroker、tasks、time、UDP 拼接文件仅保留后追加的 SVG 文档，不重绘、不改色。
- 设备列表：设备总数→`device`、在线设备→`devices_online.svg`、需关注→`devices_warning.svg`（28 px）；右上 MQTT→`mqtt`（24 px）。统计口径不变，需关注仍包含非在线设备。
- `HomePage.set_theme` / `DevicesPage.set_theme` 仅刷新图标与样式，不重建卡片或业务数据，不重置统计、筛选、选中设备、编辑/删除选择、MQTT 消息或子系统状态。首页继续使用全览 4/2/1、子系统 3/2/1 列布局。
- 顶栏顺序为原导航、主题按钮、版本、`icons/lab_logo/logo.png`、AADCL。Logo 保持比例居中于 32×32 px，文字 14 px 加粗并跟随主题；保留最小宽度 800 px、左侧 CCS 标识及全屏行为。
- 验证使用临时设备、地图、任务仓储配置，不启动真实端侧服务；资源需通过准确文件名、单一 XML 根节点、Qt 渲染、PNG 非空及异目录加载测试。截图覆盖首页/设备页、日/夜、1280/1024/800 px。
- 平台运行时、项目元数据及版本测试同步为 v0.22.9；端侧版本和通信协议独立，不随本次 UI 补丁改变。


### v0.22.9 增量验收（2026-09-03）

- 命令：`python -m unittest tests.test_app_icons tests.test_ui tests.test_v015_system_and_migration tests.test_version -v`；最终 38 项全部通过（281.602 秒）。当前虚拟环境无可执行 pytest 模块，使用测试原生 unittest 运行器。
- 首页/设备页的 day/night × 1280/1024/800 px 共 12 张截图，以及 2 张窄屏首页滚动补充截图均已目视检查；图标、文字与 AADCL 对齐正常，800 px 导航无重叠，滚动区域内的卡片保持可访问。
- 12 个追加 SVG 文档、10 个提供的 SVG 与 Logo 均完成内容保真校验；`git diff --check` 通过。
- Qt offscreen 环境会输出 OpenGL 上下文告警，本次卡片/顶栏检查不依赖三维渲染；未宣称完整测试套件、真实设备或真实服务验收。

## v0.22.8 首页状态卡语义图标（历史记录；资源路径与映射已由 v0.22.9 更新）

- 首页全览卡固定保留数值和标题。在线/离线设备复用 `icons/devices_online.svg` 与 `icons/devices_offline.svg`；地图和任务使用 `mapstorage_<mode>.svg` 与 `tasks_<mode>.svg`。
- 子系统图标仅映射 NTP/`time`、MQTT Broker/`mqttbroker`、MQTT 数据订阅/`mqtt`、UDP 高频遥测/`UDP` 和 FFmpeg/SRT/`camera`。其余子系统卡继续只显示状态圆点、标题、状态和消息。
- 主题化资源位于 `icons/app_icons/`，无主题资源由 `ccs_monitor.app_icons.asset_icon` 相对模块位置解析。图标加载失败时隐藏图形并保留全部文字与状态信息。
- `HomePage.set_theme` 只刷新卡片图标，不得重建卡片或改变统计值、子系统状态、消息及响应式布局。

## v0.22.7 联合建图、重定位与本地 odom 遥测修复

- 联合成果原子提交成功后，`MapBuildingService` 先分离并关闭全部子协调器，清除成果与融合状态，再发布 `completed` 和 `navigation_locked=False`。终态回调观察到的协调器必须已经 inactive，地图详情页据此恢复按钮、停止计时并允许切换页面。
- 重定位互斥键为当前地图 ID。`stack_starting`、`awaiting_pose` 和 `relocalizing` 期间只允许活动设备继续操作；服务层拒绝第二台设备绕过 UI 启动。成功、失败和超时均释放互斥状态。
- `PointCloudViewer.set_relocalization_picker(device_id | None)` 将十字星上下文绑定到设备；读取初始位姿时必须再次提供相同设备 ID。`start_stack` 和 `initial_pose` 继续仅通过 envelope 的 `device_id` 标识目标，不增加 payload 字段。
- 初始无会话及协商中的快照均设置 `can_download=False`，只有带 `session_id` 的 `map_required` 状态允许下发地图。重复协商不会覆盖正在活动的重定位会话。
- 本地 odom 按设备 profile 解析：Scout/WheelTech 使用 `vision_pose`，Go2 等使用 `global_pose`。当前地图轨迹最多保留 10,000 点；重定位、短暂无位姿、切换选择或同地图页面重进不清空，仅切换地图或开始新建图时清空。
- VisPy visual 使用冻结属性模型，轨迹是否有足够点数由 `PointCloudViewer._trail_visibility` 维护，不向 `Line` 实例写入动态属性；删除轨迹时同时清理 visual 和状态表。
- 指控大屏状态和两组趋势共用同一份有效本地 odom 位姿，不以 IMU 补姿态。横轴从 5 秒按 5 秒扩展至 60 秒；各分量以实线、虚线和点线区分，颜色统一来自共享设备颜色注册表。
- 本次仅升级平台至 v0.22.7。端侧 `epgeneral_relocalization` v0.2.2、`epgeneral_map_stream` v0.12.0、既有协议 schema、消息字段和端口保持不变。

## v0.22.6 / epgeneral_map_stream v0.12.0 多设备联合遥控建图

- 联合模式必须选择至少两台设备且只有一个单位外参主设备。外参方向固定为 `主设备 map <- 从设备 map`；PCD 使用 XYZ/RPY，PGM 使用 X/Y/Yaw，因此联合模式拒绝非零 Roll/Pitch。
- `RemoteMappingJobCoordinator` 为每台设备持有独立的 `RemoteMappingCoordinator(auto_commit=False)`。所有设备成果先校验暂存，再融合并通过同一个地图成果事务替换 PCD/PGM/YAML；禁止单设备成果提前覆盖目标地图。
- 端侧能力低于 0.12.0 时禁止启动联合建图。`job_id`、`role`、`primary_device_id` 在 prepare/start 和 artifact manifest 中保持一致；字段对单机可选，schema 2、artifact schema 1、协议 ID 和端口不变。
- 主设备失败必须中止其余会话；从设备失败可剔除，但最终有效设备少于两台时不得融合提交。
- 设备 ID 使用稳定颜色注册表，卡片、日志、地图 marker 和所有实时轨迹统一取色。地图自身坐标轴使用 4 px 线宽和箭头，与设备姿态轴区分。
- 建图和重定位日志左右排列。建图日志列为时间、级别、设备 ID、方向、事件、消息，支持按设备筛选、自动滚动和只清当前视图；重定位日志同样支持自动滚动与视图清除。

## v0.22.5 任务二级页交互与日志

- 设备卡点击只更新选择并保存当前本地草稿，同时关闭任务点编辑区；读取和删除不展开编辑器，只有“创建子任务”进入当前设备的航点编辑流程。
- 任务页不再维护独立选点图层下拉框，直接读取 `PointCloudViewer.layer_mode`。`grid` 保留 free-cell 校验，`overlay` 规范化为 `pointcloud` 写入既有 `layer_mode` 字段。
- 设备卡只显示身份、航点数和 revision。端侧 `task_summary` 的全部状态与消息由 `TaskRepository` 持久化为事件，并通过 `events_updated(task_id)` 通知任务页刷新。
- 日志视图使用只读、定宽、无换行的 `QPlainTextEdit`，按时间聚合审计及全部执行事件并自动滚动。清除操作只记录本次打开任务的显示截止时间，不删除 JSONL，重新打开任务恢复历史。
- 保存下发按钮由服务可用、地图已复核、编辑器已打开、至少两个航点且未选点共同决定。单设备执行按钮按执行会话在绿色“执行任务”和红色“终止任务”之间切换，`stopping` 阶段禁用。
- 本次仅升级平台至 v0.22.5；任务文件 schema、端侧 `epgeneral_task_control` v0.4.3、`ccs-task-control-v2` 消息格式和 UDP 14563/14564 端口不变。

## v0.22.4 指控大屏显示与画布生命周期修复

- 设备选择时以遥测仓储中的当前快照初始化趋势缓冲。折线显式使用主题色和固定线宽，单样本显示数据点，避免等待第二帧期间坐标系内无可见内容。
- `PointCloudViewer` 使用固定地图层和设备层顺序；设备形状、回退 marker、朝向轴及轨迹统一关闭深度遮挡并置顶，PGM 保持地图底层。
- 光标坐标能力可按查看器实例关闭。指控大屏隐藏该选项与读数，地图页和任务页继续使用全局持久化设置。
- 查看器在 Qt 事件过滤器中消费原生 VisPy 画布的 Esc，防止其关闭 OpenGL 子控件；大屏据此退出全屏，并在窗口状态恢复后重新选择和刷新地图画布。

## v0.22.3 主题化导航与下发图标

- 顶部导航固定使用 `home/device/map/mission/bev` 图标，地图下发和任务保存下发固定使用 `upload` 图标；资源均按 `<name>_day.svg` / `<name>_night.svg` 命名。
- 导航和下发按钮保留可见文字。主题切换只能刷新图标与样式，不得改变当前页面、导航选中状态、按钮启用状态或业务信号连接。

## v0.22.2 Scout 航点可达性与启动延迟

- 导航栈在任务下发后已经常驻并完成 ready 握手，统一执行只需覆盖 UDP 调度传播；`group_start_delay_seconds` 从 30 秒改为 3 秒。
- Scout 准备阶段按 `map.yaml` 的 origin/resolution/free threshold 读取 PGM。目标单元格必须是已知自由空间，未知、占用和地图外目标返回 `WAYPOINT_NOT_TRAVERSABLE`。
- action 状态 4 且文本包含规划失败时返回 `NAVIGATION_PLAN_FAILED`，平台日志保留 `move_base` 原始状态文本。

## epgeneral_task_control v0.4.2 TF 监听修复

Scout 适配器的 `tf2_ros.Buffer` 必须与生命周期一致的 `tf2_ros.TransformListener` 配套。仅创建 buffer 不会订阅 `/tf` 或 `/tf_static`，即使系统 TF 树存在 `map<-odom`，适配器查询仍会永久失败。v0.4.2 创建并持有 listener，由实时定位预检继续决定导航准备状态。

## v0.22.1 执行会话创建约束

- “执行任务”不承担保存或下发职责，不得隐式增加 revision；当前子任务必须已 delivered 且 edge revision/status 为当前 revision/ready。
- ready 检查必须先于 execution snapshot 和设备锁创建。检查失败只提示保存下发、导航准备中或端侧具体错误，不残留 `_device_execution`。
- Scout 的 TF2 查询与位姿转换异常必须归一为 `LOCALIZATION_UNAVAILABLE` feedback，禁止异常逃出 ROS callback。

## v0.22.0 Scout 导航准备与常驻生命周期

- `task_commit` ACK 只确认文件已原子保存；端侧随后通过内部 `PREPARE` 启动导航，适配器反馈 ready 后才发送 `task_summary=ready`。
- 平台分别维护 delivered revision 和 edge ready，不能因 commit ACK 提前发送 `execute_task`；多设备统一启动必须等待全部导航就绪。
- 同地图新 revision 复用导航。完成、失败及常规停止取消目标并发零速度但保留导航；`UNLOAD`、删除、急停和关闭停止进程。
- 定位暂时不可用和导航准备失败保留任务，端侧按 `preparation_retry_seconds` 自动重试；导航进程意外退出上报 `NAVIGATION_PROCESS_EXITED`。

## v0.21.2 主题化操作图标

- 内置操作图标固定存放在仓库根目录 `icons/app_icons/`，运行时由 `ccs_monitor.app_icons` 相对模块位置解析，禁止依赖进程当前工作目录。
- 通用图标使用 `<name>_day.svg` / `<name>_night.svg`；主题按钮按当前主题分别使用 `indoor_day.svg` 和 `outdoor_night.svg`，图标表达点击后的目标主题。
- 返回按钮保留“返回”文字；主题和展开/收起按钮为纯图标，但必须维护 Tooltip 与 accessibleName。
- 纵向折叠控件复用 expand/close 图标并旋转 90 度。所有控件在 `set_theme` 中只刷新图标，不得重置导航、地图、任务、视频、全屏或折叠状态。

## v0.19.2 初始位姿选择器修复

- `RelocalizationReticle` 与地图内容通过 `QStackedLayout.StackAll` 共享视口几何，透明且不接收鼠标事件；不要再将覆盖控件作为 `QStackedWidget` 的非托管子控件。
- 初始位姿的中心点使用 `_screen_to_map()` 执行地图范围校验，屏幕上方向点使用 `_screen_to_plane()` 仅求平面投影。两点必须有限且方向向量非零，方向点无需落在地图边界内。
- 此修复仅修改地面站显示与解算，不改变 `ccs-relocalization-v1`、端侧活动地图 schema 或端侧包版本。

## v0.19.1 地图交互与重复重定位

- `set_relocalization_picker()` 是边沿触发操作；持续状态刷新只更新可见性，不能重置相机 azimuth、center 或 distance。顶视模式左键只改变 yaw，右键使用独立手势起点平移。
- 共享地图查看器在滚轮缩放前后反投影鼠标位置，并用世界坐标差补偿 camera center。地图外或无法与 `z=0` 平面求交时只调整 distance。
- 重复重定位在地面站和端侧立即废弃旧绑定。端侧状态文件 schema 2 只允许 localized 状态携带 `map_from_odom`；TF 监测代际用于拒绝旧线程迟到结果。
- Scout 成功 TF 先原子写端侧再回包；同地图协商可用持久 TF修复地面站绑定。Go2 使用相同状态清理代码，但后端保持禁用。

## v0.19.0 遥测状态与电池修复

- schema 6 的 `active_map_id` 是设备详情唯一地图上下文；运行会话状态优先，重启后由同地图绑定恢复成功状态。
- UDP v1 线格式不变，地面站配置 schema 2 按精确 hash 选择显式允许的旧/新 Scout、Go2 描述符集合。
- Scout 本地/Map 位姿来自 `/scout/odom`，FAST_LIO2 来自 `/Odometry`，`/livox/imu` 是 Livox IMU。Go2 使用 prefixed odometry 和底盘 IMU。
- 重定位节点原子写活动地图状态；UDP `pgm_file` 来源限制在 profile 地图根目录并拒绝路径逃逸和符号链接。
- Scout 电池曲线默认为空，分钟中位数保留 90 天；完整放电标定前不得从单点电压伪造 SOC。

## v0.19.0 / epgeneral_relocalization v0.1.0

- 地面站 `RelocalizationService` 独占 UDP 14566 和 TCP 14601，按 map/device/session 管理协商、地图下发、启动栈、初始位姿与结果；所有端侧回包校验登记 IP、会话和单调 sequence。
- 完整重定位地图包固定为 `manifest.json`、`public_map.pcd`、`map.pgm`、`map.yaml`。端侧续传到临时文件，拒绝重定向、路径穿越、符号链接、额外条目和哈希不一致，再原子替换 `<map_root>/<map_id>`。
- 设备配置 schema 5 保存 `relocalization_profile` 和每地图 `map_from_odom`；地图页、任务页及指控大屏使用 profile 指定的 UDP pose source 组合实时位姿。
- Scout 初始位姿使用固定中心反投影计算 X/Y/yaw，端侧发布 `/initialpose` 后要求 10 个 10 Hz TF 样本满足 0.10 m / 2° 稳定窗口。
- Go2 只有禁用 profile 和 `~/go2_mid360_nav/maps/ccs_download` 目录约定；没有全局重定位算法包前始终返回 `UNSUPPORTED_BACKEND`。

## epgeneral_map_stream v0.9.1

- `mapping_prerequisites.launch` 在坐标转换链后启动 `go2_map_accumulator/map_accumulator.launch`，`start_fast_lio.sh` 等待 `/go2_map_accumulator` 后才宣布建图栈就绪。

- stop 成果事务拆分为 `save_map -> require_fresh_file -> stop_fast_lio -> generate_pgm`，新鲜度校验发生在建图进程终止前。
- 保存服务、超时或 PCD 校验失败时调用 abort 清理受控进程组；start 不再删除 accumulator 的既有 PCD。

## epgeneral_map_stream v0.8.1

- 删除 `_wait_for_fast_lio_outputs()` 的零时间戳 TF lookup；FAST_LIO 输出与坐标转换节点就绪即可完成 start。
- `odom <- lio_odom` 仅保留在 `_process_cloud()`，随实际 cloud header 时间戳查询、记录并用于 PCD 点坐标转换。

## v0.18.3 / epgeneral_map_stream v0.8.0

- `MapDetailPage` 使用水平 splitter 承载可收起在线设备栏；只渲染 MQTT `ONLINE` 设备，当前建图设备置顶。每个设备显示快照字段、电量进度和现有 `SrtVideoWidget`，由侧栏仲裁为最多一路视频，收起、离线或离页时停止。
- 地图设备位置只读取 `UdpTelemetryStore.global_pose` 最新快照，100 ms 渲染定时器避免高频信号逐包重绘。2 秒以上位姿不显示；同 frame 使用恒等变换，否则只接受 `build_provenance.transforms` 中唯一且设备 ID 精确匹配的外参。
- viewer 的设备标记、选中设备 XYZ 轴和最多 10,000 点轨迹受独立“设备”图层控制。轨迹按 0.02 m 位移阈值追加，设备、地图或变换变化时清空。
- 静态 PCD 与实时预览共享可见点集 Z 范围，NumPy 向量化生成红、黄、绿、青、蓝、紫逐点 RGBA；主题切换不再覆盖高度颜色。
- 坐标生命周期拆分为 `lio_odom` FAST_LIO 源、`odom` 实时预览和 `map` 最终成果定义。
- 端侧在窗口最后一帧时间戳查询 `odom <- lio_odom`，先统一窗口到 `lio_odom`，再把实际点坐标转换至 `odom`；描述符记录同一份规范化 TF。
- 平台分别校验实时分片 `odom` 和端侧成果 `lio_odom`，成果完整验收后才重标为 `map`。

## epgeneral_map_stream v0.7.2

- `start_fast_lio.sh` 先启动 FAST_LIO 并等待 `/laserMapping`，然后拉起统一坐标转换 launch 并等待四个转换节点。
- 外层启动截止时间覆盖两个串行就绪阶段，子进程仍由同一 supervisor 和进程组管理。

## epgeneral_map_stream v0.7.1

- 将 1.5 秒传感器消息探测与 ROS launch 集成预检拆分，端侧集成预检独立使用 8 秒上限。
- 协商失败原因写入 UDP 前限长，完整命令和异常继续写入 `map_stream.log`，避免 `prepare_result` 超过 1400 字节。

## epgeneral_map_stream v0.7.0

- 配置 schema 4 新增 `integrations.mapping_prerequisites`，固定使用 Go2 MID360 工作空间 setup 和设备外参 YAML。
- 包内 `mapping_prerequisites.launch` 将统一参数 `extrinsics_file` 映射到 `go2_tf_manager`、`go2_pose_adapter` 的 `extrinsics`，并启动两路 cloud frame adapter。
- `start_fast_lio.sh` 通过 supervisor 等待四个转换节点注册后再启动 FAST_LIO；所有进程共享 PGID，任何必需子进程退出都会触发整组清理。

## v0.18.2

- `TelemetrySampler` 在高频样本进入窗口前校验全部数值和四元数范数；非法来源独立降级为 `valid=false`，其余 Level 1 descriptor 保持发送。
- 端侧 diagnostics 保留 `udp_tx` 并增加逐来源统计；地面站拒收告警按原因累计且 5 秒限频，避免坏报文以 20 Hz 淹没日志。
- 设备详情仍只缓存最新 UDP 快照并按 50 ms 绘制，高频信号不直接触发布局或逐帧重绘。

## v0.18.1

- v2 实时预览不再在 UDP 接收线程解压并逐点更新 500 万项 Python 体素字典。端侧每秒原子生成二进制 PCD，平台后台下载并用 NumPy 向量化体素去重，预览固定上限 30 万点、下载队列上限 4。
- `cloud_fragment_ready` 描述符绑定 map/device/session、分片号、时间范围、点数、frame、URL、字节数、SHA-256 和到期时间；成功解析和合并后才发送 `cloud_fragment_ack`。
- FAST_LIO 启动前记录固定源 PCD 的 size/mtime/inode/SHA-256，停止后要求 mtime 不早于 session 且指纹变化，未更新时终止成果事务。
- SRT 首帧先显示画布再进入 playing，playing 只发送一次；FFmpeg stderr 有界并进入设备日志，无首帧时按 7 秒超时重试。
- 选点模式继续拦截左键创建任务点，但单独处理滚轮以调整俯视相机距离。

## v0.18.0

- Windows UDP 接收缓冲增至 4 MiB，并通过 `request_cloud_chunks` 对压缩点云帧执行缺片补发；完整帧仍须通过分片齐全、CRC32、zlib 解压长度和有限坐标校验。
- 建图活性分别检查完整点云和端侧心跳：10 秒无完整帧进入警告，只有 30 秒无完整帧且心跳也超过 5 秒中断才失败。
- `abort_mapping` 使用无成果清理流程停止 FAST_LIO、注销 ROS 订阅并清空缓存，禁止在成果生成阶段强制中断。

## v0.17.0

### 遥控建图会话恢复与调试

- `RemoteMappingCoordinator` 按命令使用独立截止时间：prepare 10 秒，start 45 秒，活动会话恢复 45 秒。UDP 重试预算耗尽不再提前结束等待，截止时间内的晚到 ACK 仍可完成命令。
- start 首先接收 `session_status=starting`，端侧在 FAST_LIO 注册点云与里程计就绪后才返回成功 ACK；失败返回 `COMMAND_FAILED` 和子进程或话题探测原因。
- 重新协商在原 session 上发送 `restart_active=true`。端侧对 ready/starting/mapping/error 注销订阅、清空缓存、停止 FAST_LIO、删除 PID 和会话临时成果，再重新执行 prepare；stopping/generating/serving 不可强制中断。
- `prepare_result` 可选返回 `restarted`、`previous_state`、`active_session_id`，旧 v2 对端可忽略这些字段。恢复路径不运行正常 stop，不生成 PCD/PGM/YAML。
- UI 日志按时间展示 TX/RX/LOCAL、命令、重试、短 session ID、ACK 和错误；每个 session 上限 200 条，点云按窗口限频汇总，重新协商保留日志。
- 建图实时阶段以 `lio_odom` 显示和校验，成果 ZIP 完整下载并提交后才使用 `map`。`/livox/imu` readiness 只验证类型和一条新数据，不检查 payload 完整性。
- `epgeneral_map_stream` v0.4.1 对 FAST_LIO 点云/里程计分别到达的回调增加最多 3 帧的待匹配队列。匹配仍使用 50 ms header 时间窗，不用上一帧约 100 ms 的旧位姿掩盖竞态；真正丢帧记录原因与时间差。

## v0.16.1

### 遥测序列代际

- `epgeneral_mqtav` v0.3.1 每次启动生成 UUID `session_id`，同一进程的 presence、heartbeat、status 共用该值。指控平台按规范设备 ID、session 和消息类型维护 sequence；新 session 原子退役旧 session。
- MQTT schema 仍为 1.0，`session_id` 为可选字段。旧端侧通过 retained online presence 重置序列窗口；QoS 1 同 sequence 重复投递幂等忽略。
- `UdpTelemetryStore` 将报文 ID 解析为设备配置中的规范 ID，并保留最近退役 session，避免延迟数据把 tracker 切回旧进程。页面读取、信号和日志始终使用规范 ID。
- Go2 EDU 的两条官方启动路径必须显式传入同一 profile。bringup 通过 `ground_station_ip` 参数设置 UDP 目标；一键脚本向 bridge、MQTT、UDP 和 SRT launch 传入 profile 文件，并在启动前验证文件存在，避免静默回退到通用 UAV 配置。
- `PointCloudViewer` 的画布选点使用 VisPy 完整投影矩阵逆变换。逆投影结果必须从齐次坐标除以 `w` 后再与 `z=0` 平面求交；PCD 使用点云 XY bounds，PGM 使用 origin/yaw/分辨率定义的旋转 footprint 拒绝图外选点。任务图层下拉框同步切换查看器图层。

## v0.16.0

### 单机遥控建图

- `RemoteMappingCoordinator` 与 v1 `MapBuildingService` 共享 UDP 14562 socket，但使用独立 `ccs-map-stream-v2` schema 2 解析与状态；v2 失败不回退 v1。
- 准备阶段由端侧统一检查 Livox 点云、Livox IMU、成果存储和地图生成能力，平台仅在逐项通过且 frame 匹配后开放开始按钮。
- 实时 `cloud_chunk` 仅用于预览。停止 ACK 后必须等待端侧 `artifact_status=ready`，最终真值只来自下载的 PCD+PGM+YAML。
- HTTP 下载限制 URL 主机与设备 IP 一致、禁止重定向，并执行 Range 续传、ZIP 路径/符号链接/压缩比/未声明文件校验。
- 地图仓储在同一事务中替换 `map.pcd`、`map.pgm`、`map.yaml` 和 `map.json`；失败恢复旧图层并保留下载检查点。
- 端侧 `epgeneral_map_stream` v0.3.0 使用 v2 schema 2 和配置 schema 3；start 先启动 FAST_LIO 并验证注册点云/里程计，stop 后依次停止 FAST_LIO、生成 PGM/YAML、校验和打包。
- 三个 Bash 包装器只接受参数数组，负责 source 工作空间、roslaunch、进程组/PID 与超时；默认 FAST_LIO/PGM 配置是占位符，真实设备部署时必须替换。

### 共享地图显示

- `PointCloudViewer` 内置图层、XY 网格、刻度与光标坐标控件，因此地图页、任务页与指控大屏共享一致行为。
- 显示参数写入 `QSettings` 并通过共享信号同步；对过密网格和刻度设置安全的绘制数量上限。

## v0.15.1

### 融合算法路径生命周期

- v0.13.2 只保证注册表写入相对安装根目录的路径，但运行时 `MapFusionAlgorithm` 仍包含解析后的绝对路径；建图服务此前直接接受该对象，使旧安装创建的对象可以把绝对路径传入融合工作进程。
- 注册表现在只保存相对于 `data/map_fusion_algorithms` 的路径。`StaticPathResolver` 同时识别 Windows 与 POSIX 绝对路径语法，并按文件名从当前受管目录恢复旧配置。
- `MapBuildingService` 在启动任务前始终按 `algorithm_id` 从自身 `MapFusionRepository` 重新读取算法，运行时绝对路径仅存在于当前进程和临时 worker 请求中，不再作为可迁移状态保存或跨安装复用。

## v0.15.0

### 运行状态与设备生命周期

- `SystemRuntimeStatusStore` 将 NTP、MQTT、UDP、建图、任务、FFmpeg/SRT 和本地仓储状态汇总为统一枚举；首页只订阅状态仓储，不直接持有网络服务。
- FFmpeg/SRT 能力在应用启动后通过独立 `QProcess` 执行一次协议探测，缺少程序或 SRT 输入协议仅标记对应卡片故障。
- 设备详情按设备 ID 大小写不敏感缓存最新 UDP 快照，50 ms 定时器只绘制脏快照。日志通过 `logs_changed` 汇总 MQTT、UDP 和 SRT 状态变化。
- `DeviceReferenceMigrationCoordinator` 在修改设备 ID 时暂存地图和任务定义，更新当前 `creator_devices` 与未归档子任务；失败时恢复原定义。执行历史和旧审计不可变。
- UI 使用“运行模式”描述 MQTT `flight_mode`，协议中的 `flight_mode`、`armed` 与 `system_status` 均未删除。
- 单机建图固定使用默认算法；大屏右栏的 `user_collapsed` 优先于 MQTT 快照刷新和响应式展开。

## v0.14.0

### SRT 视频链路

- `srt_video.py` 使用 `QProcess` 直接启动系统 FFmpeg，先读取 `ffmpeg -protocols` 并确认输入协议含 `srt`，不再依赖 PySide6 自带 FFmpeg 或 Qt Multimedia。
- FFmpeg 将 H.264/MPEG-TS 解码为固定 RGBA rawvideo；接收器缓存不完整 stdout 分片，每次 UI 刷新只提交最新完整帧。设备切换、切页和退出会终止进程及重试。
- `devices.json` schema 4 增加 `srt_port` 与 `srt_latency_ms`；schema 1–3 原子迁移为 9000/120 ms。配置中的 FFmpeg 静态路径只能相对软件根目录。
- `epgeneral_video_srt` v0.1.0 在 Noetic/GStreamer 1.16+ 上作为 Listener 输出 baseline H.264/MPEG-TS，地面站作为 Caller；管线检查 `mpegtsmux` 与 `srtsink`。

## v0.13.2

### 可迁移静态资源

- `StaticPathResolver` 以配置目录和受管资源目录的公共父目录为存储根，配置中仅写入 POSIX 相对路径；页面和融合工作进程仍获得解析后的绝对路径。
- 融合算法导入和设备图标上传先校验，再复制到 `data/map_fusion_algorithms` 或 `data/device_type_assets`；已配置的静态文件作为部署资源保留。
- 加载旧绝对路径时优先按文件名查找当前安装目录副本，成功后自动回写相对路径。路径解析不依赖进程工作目录，并拒绝越出受管目录的资源。

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

- 历史 Windows localhost 测试覆盖 v1 start/ACK/cloud/stop/ACK；当前端侧 `epgeneral_map_stream` v0.3.0 已通过 v2 协议、处理、成果、HTTP Range 与 UDP 契约测试，真实 ROS Noetic Livox/FAST_LIO/PGM 联调仍需在目标设备执行。
- 端侧 `epgeneral_map_stream` 从 v0.2.0 升级为 v0.3.0；其余端侧 ROS 包版本不变。

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

- `edge_side_pkg` 的端侧发布 allowlist 固定为 device_config、map_stream、mqtav、relocalization、task_control、udp_telemetry 和 video_srt 七个 ROS 包。
- `epgeneral_device_config/config/` 是设备身份及六类运行 YAML 的唯一端侧入口；功能包内部不再保存运行配置。
- `deploy` 保存设备 profile 原件，`documents` 保存部署指南和记录；两者均留在指控端，不进入端侧 catkin `src`。

### ROS 图像 SRT 推流

- `epgeneral_video_srt` v0.1.1 使用 C++、`image_transport`、`cv_bridge` 和 GStreamer，订阅配置的原始或压缩 ROS 图像话题。
- 编码管线为 baseline H.264/MPEG-TS/SRT Listener，默认监听 UDP 9000；摄像头驱动由设备自身启动。
- 运行参数来自 `epgeneral_device_config/config/video.yaml`，设备专用参数仅保存在对应 deploy profile。

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
## v0.20.1 任务界面修正

- `MapRepository` 使用 `data/map_server/active_map.json` 保存全局激活地图；指控大屏地图选择写入该状态，地图页和任务页只读同步展示。
- 任务绑定地图与全局激活地图可以不同，任务不会因全局地图切换自动改写。
- 任务设备操作收拢到设备卡片内部，右侧点列表在未选中设备时保持收起；选点按钮位于右侧点列表。
- 点云高度光标保留 `z <= threshold` 的点，滑块向下调整时隐藏更高点云。

v0.20.1 任务页交互约束：设备列表使用约 280px 起步宽度的卡片，子任务创建、读取、删除按钮绑定在各自卡片内；进入任务页不自动选择设备，右侧点列表保持收起，选择设备或点击卡片操作后才展开。地图工具栏不提供选点入口，选点按钮位于右侧点列表底部，并显示“开始选点/结束选点”；保存下发按钮显示“保存下发”，设备执行按钮显示“执行任务”。

## v0.21.1 Scout 任务执行修正

- 任务页中间操作按钮承载“开始选点/结束选点”，右侧保留“执行任务”。
- Scout 任务适配器在启动导航前必须收到实时 `/fastlio_odom`，并能查询 `map<-odom` TF；只存在持久化 `localized` 文件不能作为执行依据。
- `epgeneral_relocalization` 重启时将旧 `localized` 状态降级为 `standby`，重新定位成功后才可执行任务。平台保留端侧失败消息和错误码。

## v0.21.0 Scout 任务执行

- Scout 任务控制包为 v0.3.0；协议和 UDP 端口不变。适配器收到执行命令后按任务地图启动 `scout_navigation/navigation_teb.launch`，使用 `/fastlio_odom` 与 `map<-odom` TF 计算 map 位姿。
- 任务地图必须同时匹配平台全局激活地图和端侧 `relocalization.json` 的 localized 地图；不一致时拒绝运动。
- 目标通过 `/move_base/goal` 顺序执行；航点成功由 move_base action result 判定，状态和航点进度继续从 14564 回传。该段为 v0.21.0 历史行为，v0.22.0 已改为 commit 后准备并常驻导航。
- Scout 一键启动常驻任务控制节点；v0.22.0 起导航栈随已提交任务保持运行，删除、急停或关闭时卸载。尚不宣称真实车辆运动已验收。
