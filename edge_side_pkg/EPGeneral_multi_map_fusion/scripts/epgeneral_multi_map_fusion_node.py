#!/usr/bin/env python3
"""Catkin entrypoint with source-workspace import fallback."""

import os
import sys


_SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if os.path.isdir(os.path.join(_SOURCE_ROOT, "epgeneral_multi_map_fusion")):
    sys.path.insert(0, _SOURCE_ROOT)

from epgeneral_multi_map_fusion.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
