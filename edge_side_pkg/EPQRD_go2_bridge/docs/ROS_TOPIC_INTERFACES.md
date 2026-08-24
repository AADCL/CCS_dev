# epqrd_go2_bridge ROS 话题接口

接口版本：`epqrd_go2_bridge v0.2.0`。设备 ID 为 `QRD_001`、`topic_prefix=/qrd` 时，公共命名空间为 `/qrd/QRD_001`。修改配置后，以下名称中的设备 ID 会自动变化。

## 数据来源与时间

节点只读订阅 Unitree SDK2 `rt/lowstate` 和 `rt/sportmodestate`，不发送运动命令。新增消息的 `header.stamp` 是 DDS 帧在桥接机上的 ROS 接收时间；同一源帧拆分出的消息具有相同 `header.seq` 和 `header.stamp`。`SportModeStatus.source_stamp` 保存 Go2 提供的原始时间，非法源时间写为零。原始话题不裁剪或归一化数值。

LowState 完整话题默认限频 100 Hz，SportModeState 完整话题默认限频 50 Hz，可通过 `rates/low_state_hz` 和 `rates/sport_mode_hz` 修改。数组顺序严格沿用 SDK2 commit `ce14ddccbc29fe6b54ad736c89f01849f0093834`。

## 完整状态话题

| 话题 | 类型 | Go2 来源 | 内容 |
| --- | --- | --- | --- |
| `/qrd/QRD_001/low_state/info` | `epqrd_go2_bridge/LowStateInfo` | `rt/lowstate` | head、level_flag、frame_reserve、sn、version、bandwidth、tick、bit_flag、adc_reel、两路 NTC、power_v/a、4 路 fan_frequency、reserve、crc |
| `/qrd/QRD_001/low_state/imu` | `epqrd_go2_bridge/ImuState` | `LowState.imu_state` | quaternion `[w,x,y,z]`、gyroscope `[x,y,z]`、accelerometer `[x,y,z]`、rpy `[roll,pitch,yaw]`、temperature |
| `/qrd/QRD_001/low_state/motors` | `epqrd_go2_bridge/MotorStateArray` | `LowState.motor_state[20]` | 每路 mode、q/dq/ddq、tau_est、q/dq/ddq_raw、temperature、lost、reserve[2] |
| `/qrd/QRD_001/low_state/bms` | `epqrd_go2_bridge/BmsState` | `LowState.bms_state` | 版本、status、soc、current、cycle、BQ/MCU NTC 及 cell_vol[15] |
| `/qrd/QRD_001/low_state/foot_force` | `epqrd_go2_bridge/LowStateFootForce` | `LowState.foot_force/foot_force_est` | 四足测量力和估计力 |
| `/qrd/QRD_001/low_state/wireless_remote` | `epqrd_go2_bridge/WirelessRemote` | `LowState.wireless_remote[40]` | 遥控器原始字节，不在桥内解析 |
| `/qrd/QRD_001/sport_mode/status` | `epqrd_go2_bridge/SportModeStatus` | `rt/sportmodestate` | DDS 源时间、error_code、mode、progress、gait_type、foot_raise_height、body_height |
| `/qrd/QRD_001/sport_mode/imu` | `epqrd_go2_bridge/ImuState` | `SportModeState.imu_state` | 完整高层 IMU，布局同 LowState IMU |
| `/qrd/QRD_001/sport_mode/kinematics` | `epqrd_go2_bridge/SportModeKinematics` | `SportModeState` | position[3]、velocity[3]、yaw_speed；位置为 Go2 局部里程计 |
| `/qrd/QRD_001/sport_mode/obstacle_ranges` | `epqrd_go2_bridge/ObstacleRanges` | `SportModeState.range_obstacle[4]` | SDK 原始四方向障碍距离；方向索引按对应固件/SDK 定义使用 |
| `/qrd/QRD_001/sport_mode/feet` | `epqrd_go2_bridge/SportModeFootState` | `SportModeState` | foot_force[4]、foot_position_body[12]、foot_speed_body[12]，后三元组按 SDK 足序排列 |
| `/qrd/QRD_001/sport_mode/path` | `epqrd_go2_bridge/PathPointArray` | `SportModeState.path_point[10]` | 每点 t_from_start、x、y、yaw、vx、vy、vyaw |

## 兼容话题

| 话题 | 类型 | 来源/用途 |
| --- | --- | --- |
| `/qrd/QRD_001/battery` | `sensor_msgs/BatteryState` | LowState power_v、power_a 和 BMS SOC，默认 1 Hz |
| `/qrd/QRD_001/imu` | `sensor_msgs/Imu` | SportModeState IMU，四元数归一化并附配置协方差 |
| `/qrd/QRD_001/odometry` | `nav_msgs/Odometry` | SportModeState 局部 position、velocity、yaw_speed 和姿态 |
| `/qrd/QRD_001/robot_mode` | `std_msgs/String` | mode/gait/error 变化时锁存发布 |
| `/qrd/QRD_001/link/sdk` | `std_msgs/Bool` | 两个 DDS 来源均在超时内时为 true，1 Hz |
| `/qrd/QRD_001/heartbeat` | `std_msgs/Header` | 桥接节点本地心跳，1 Hz |
| `/qrd/QRD_001/diagnostics` | `diagnostic_msgs/DiagnosticArray` | DDS 年龄、电源、SOC 和 SportModeState 错误码 |

## 使用与订阅

查看类型、频率和单帧内容：

```bash
rostopic type /qrd/QRD_001/low_state/motors
rostopic hz /qrd/QRD_001/sport_mode/kinematics
rostopic echo -n 1 /qrd/QRD_001/low_state/bms
rostopic echo -n 1 /qrd/QRD_001/sport_mode/path
```

Python 订阅示例：

```python
import rospy
from epqrd_go2_bridge.msg import SportModeKinematics

def on_state(message):
    rospy.loginfo("position=%s velocity=%s", message.position, message.velocity)

rospy.init_node("go2_state_consumer")
rospy.Subscriber("/qrd/QRD_001/sport_mode/kinematics", SportModeKinematics, on_state, queue_size=10)
rospy.spin()
```

C++ 订阅使用 `nh.subscribe<epqrd_go2_bridge::MotorStateArray>(topic, queue_size, callback)`，并在消费包的 `package.xml`/`CMakeLists.txt` 中声明对 `epqrd_go2_bridge` 的依赖。需要关联同一源帧的多个拆分话题时，以相同的 `header.seq` 和 `header.stamp` 配对；LowState 与 SportModeState 是独立 DDS 流，不能按序号互相配对。
