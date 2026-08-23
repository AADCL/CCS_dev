# Changelog

<!-- epqrd_go2_bridge_VERSION: 0.2.0 -->

## [0.2.0] - 2026-08-23

- Added strongly typed ROS messages for every field in SDK2 LowState and SportModeState.
- Added twelve prefixed semantic state topics with configurable 100 Hz and 50 Hz limits.
- Added a complete ROS topic interface guide and retained all v0.1.0 topics unchanged.
- Deployed to QRD_001 and verified all twelve new topics against live DDS data.

## [0.1.0] - 2026-08-18

- Added a read-only Unitree SDK2 bridge for Go2 EDU LowState and SportModeState.
- Added standard ROS battery, IMU, odometry, mode, heartbeat, SDK link, and diagnostics topics.
