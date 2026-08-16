#!/usr/bin/env python3
"""ROS entrypoint with source-workspace import fallback."""

import os
import sys


_SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if os.path.isdir(os.path.join(_SOURCE_ROOT, "epgeneral_map_stream")):
    sys.path.insert(0, _SOURCE_ROOT)

from epgeneral_map_stream.node import run


if __name__ == "__main__":
    run()
