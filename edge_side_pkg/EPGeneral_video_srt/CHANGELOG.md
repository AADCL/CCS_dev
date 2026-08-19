# Changelog

## [0.1.0] - 2026-08-18

- Added configurable raw and compressed ROS image subscriptions.
- Added baseline H.264/MPEG-TS encoding and SRT Listener output on UDP 9000.
- Added frame watchdog, required GStreamer element checks, and pipeline diagnostics.
- Improved startup diagnostics for missing SRT plugins and normalized wildcard Listener URI
  generation to `srt://:<port>?mode=listener`.
- Fixed SRT latency conversion from configured milliseconds to URI microseconds and mirrored
  fatal startup errors to stderr for roslaunch diagnostics.
