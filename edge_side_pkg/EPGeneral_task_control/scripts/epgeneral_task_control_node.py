#!/usr/bin/env python3
import os
import sys

SOURCE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if os.path.isdir(os.path.join(SOURCE_ROOT, "epgeneral_task_control")):
    sys.path.insert(0, SOURCE_ROOT)

from epgeneral_task_control.node import run

if __name__ == "__main__":
    run()
