# EPGeneral_multi_map Step 6 Baseline Test Result

**Date:** 2026-08-18  
**Workspace:** `C:\Users\BM\Desktop\重点研发\CCS_dev-main`  
**Purpose:** 在创建 `EPGeneral_multi_map` 之前记录地面站和旧端侧包的可复现基线。

## 1. Source and environment

- 地面站版本：`ccs_monitor.version.__version__ == "0.13.1"`。
- 当前目录不含 `.git`，无法确认 branch、HEAD、remote 或未提交差异，也不能建立 Git worktree；按项目负责人指定在该源码快照原地执行测试。
- 相比此前 `CCS_dev_0817` 快照：排除 `.git`、`__pycache__`、`artifacts`、`data` 后，共发现 13 个新增文件、28 个同路径内容变化和 4 个仅存在于旧工作区的项目文档。
- 重要环境变化：新增多设备 `_ActiveJob`、每设备 session、地图融合 worker、`epgeneral_multi_map_fusion`、PGM 融合及相关测试。
- 当前主要解释器：Python 3.11.4。
- 可用兼容解释器：Python 3.8.8，路径 `C:\Users\BM\3D Objects\anaconda\python.exe`。
- `python -m pip check`：通过，未发现已安装包依赖冲突。
- Windows 环境没有 ROS、WSL、`roscore`、`roslaunch` 或 `catkin_make`。

## 2. Python compile

Command:

```powershell
python -m compileall -q ccs_monitor run.py tests
```

Result: PASS，退出码 0。

Python 3.8 旧端侧语法检查：

```powershell
& 'C:\Users\BM\3D Objects\anaconda\python.exe' -m compileall -q `
  edge_side_pkg/EPGeneral_map_stream/src `
  edge_side_pkg/EPGeneral_map_stream/scripts `
  edge_side_pkg/EPGeneral_map_stream/test
```

Result: PASS，退出码 0。

## 3. Ground-station unit and integration tests

Command:

```powershell
python -m unittest discover -s tests -v
```

Result: PASS。

```text
Ran 147 tests in 267.005s
OK (skipped=2)
```

两项跳过均为 Open3D 插件测试，原因是当前 Python 环境未安装 `open3d`。NumPy RANSAC、内建 `epgeneral_multi_map_fusion`、多设备 start 扩展、ACK barrier、地图保存及其他地面站测试均通过。

## 4. Existing edge package tests

Python 3.11.4:

```powershell
$env:PYTHONPATH = 'edge_side_pkg/EPGeneral_map_stream/src'
python -m unittest discover -s edge_side_pkg/EPGeneral_map_stream/test -v
```

Result: PASS，19 tests，0 failures。

Python 3.8.8:

```powershell
$env:PYTHONPATH = 'edge_side_pkg/EPGeneral_map_stream/src'
& 'C:\Users\BM\3D Objects\anaconda\python.exe' `
  -m unittest discover -s edge_side_pkg/EPGeneral_map_stream/test -v
```

Result: PASS，19 tests，0 failures。

## 5. ROS build

`catkin_make` 未执行。当前机器是 Windows，且没有可用 WSL/Ubuntu 20.04、ROS1 Noetic 或 Catkin。此项属于环境限制，不是代码失败；必须在 Ubuntu 20.04 + ROS1 Noetic + 系统 Python 3.8 环境补做。

## 6. Known baseline warnings and limitations

- 无显示环境产生 `QOpenGLWidget`/OpenGL context 警告，但相关测试通过。
- 部分测试设备图标仍引用旧绝对路径 `D:/Projects/AI_assisted/CCS_dev/data/device_type_assets/...`，产生文件无法打开警告，但相关测试通过。
- MQTT 端口冲突测试会按预期打印一次 broker bind 异常，测试本身通过。
- Open3D 不可用导致 2 项测试跳过；不影响本次端侧包，因为冻结范围明确不引入 Open3D。
- 当前目录没有 Git 元数据，Step 11 前必须先取得正确的 Git 仓库/分支并由项目负责人批准提交和推送。

## 7. Step 6 conclusion

满足 SOP Step 6 情况 B：所有可在当前环境执行的基线检查均通过；ROS 构建、Noetic 运行和双机器人实机验证已明确记录为环境限制。执行基线时尚未创建 `edge_side_pkg/EPGeneral_multi_map`，现有地面站和 `EPGeneral_map_stream` 业务代码未因本步骤修改。
