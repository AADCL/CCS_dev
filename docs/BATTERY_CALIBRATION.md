# 设备电池曲线校准

地面站保留 MQTT schema 1.0，不要求端侧增加字段。设备在 `config/devices.json` 中通过可选的 `battery_profile` 选择估算曲线：`disabled`、`scout_mini` 或 `wheeltec_r550p`。端侧提供 0–100 范围内的 `health.battery.percentage` 时直接使用原值；只有百分比缺失且电压有效时，才按 profile 估算并写入统一的 `DeviceSnapshot.battery_percent`。

## 估算方法

每台设备和 profile 独立保留最近 15 个有效电压样本，使用滚动中位数降低瞬时负载波动。相邻曲线点 `(V0, P0)`、`(V1, P1)` 之间采用分段线性插值：

```text
P = clamp(P0 + (V - V0) * (P1 - P0) / (V1 - V0), 0, 100)
```

低于首个点或高于最后一个点时分别钳制为端点电量。缺少 profile、profile 为 `disabled`、配置无效、电压缺失、非有限值、非正值或达到 100 V 时保持未知，不生成百分比。估算结果低于 25% 时设备健康状态进入“需关注”。

分钟级中位电压保存在 `data/battery_history/<device_id>.json`，保留两天。历史记录包含 `minute`、`profile`、`voltage_median` 和 `online`，可用于现场校准。

## 初始曲线

### WheelTech R550P

项目实测规格以 `25.5 V` 为满电，以 `20.0 V` 为建议充电阈值：

| 电压/V | 20.0 | 20.8 | 21.5 | 22.3 | 23.1 | 24.0 | 24.8 | 25.5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 电量/% | 0 | 10 | 25 | 45 | 65 | 82 | 94 | 100 |

因此 `V <= 20.0 V` 显示 `0%` 并进入低电量状态，`V >= 25.5 V` 显示 `100%`。

### Scout Mini

初始曲线按 7 串三元锂电池采用保守估计：

| 电压/V | 24.5 | 25.2 | 25.9 | 26.6 | 27.3 | 28.0 | 28.7 | 29.4 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 电量/% | 0 | 10 | 25 | 45 | 65 | 82 | 94 | 100 |

Scout Mini 官方规格只确认 `24 V 15 Ah` 三元锂电池，未公布 SOC 曲线，因此该 profile 在配置中标记为“待实测修正”。参考：[Scout Mini 官方规格](https://www.agilex.ai/page/68930913f34d776a0e9d147e?mi=0&rn=SCOUT+MINI)。

## 校准流程

1. 充满电后静置并记录开路电压，再在典型负载下运行设备至建议充电阈值。
2. 从分钟历史中剔除充电过程、离线样本和明显异常点，按实测剩余容量选取单调递增的电压/百分比锚点。
3. 编辑 `config/battery_estimation.json` 对应 profile 的 `curve`。电压和百分比都必须严格递增，至少包含两个点，百分比范围为 0–100。
4. 重启地面站后，在设备卡、详情页、地图卡片和指控大屏核对相同设备显示一致。
5. 至少完成一次满电、典型运行和低电量闭环后，才可将 Scout 曲线的 `calibration_status` 改为现场已校准说明。

旧版设备配置缺少 `battery_profile` 时，会从同名 `relocalization_profile` 兼容推断 Scout 或 WheelTech；其余设备迁移为 `disabled`。旧版电池 schema 1 会升级为通用 schema 2，已有有效 Scout 自定义曲线会保留并补齐满电端点。
