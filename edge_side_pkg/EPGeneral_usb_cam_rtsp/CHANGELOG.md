# Changelog

## [0.2.0] - 2026-08-18

- Removed automatic `usb_cam` startup; the package now consumes an existing ROS video topic directly.
- Added configurable `sensor_msgs/Image` and `sensor_msgs/CompressedImage` inputs.
- Added explicit output resolution settings with backward-compatible legacy width/height parameters.

## [0.1.0]

- Added the initial ROS image to H.264 RTSP pipeline.
