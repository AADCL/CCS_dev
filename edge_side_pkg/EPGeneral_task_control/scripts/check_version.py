#!/usr/bin/env python3
import os
import re
import sys
from xml.etree import ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from epgeneral_task_control import __version__


def main():
    manifest = ElementTree.parse(os.path.join(ROOT, "package.xml")).getroot().findtext("version")
    failures = []
    if manifest != __version__:
        failures.append("Python version does not match package.xml")
    marker = re.compile(r"<!-- epgeneral_task_control_VERSION: ([0-9]+\.[0-9]+\.[0-9]+) -->")
    for name in ("README.md", "CHANGELOG.md"):
        with open(os.path.join(ROOT, name), "r", encoding="utf-8") as stream:
            match = marker.search(stream.read())
        if match is None or match.group(1) != manifest:
            failures.append("%s version marker does not match" % name)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("epgeneral_task_control version %s is consistent" % manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
