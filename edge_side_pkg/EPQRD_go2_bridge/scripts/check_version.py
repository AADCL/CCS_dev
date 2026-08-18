#!/usr/bin/env python3
import os
import re
from xml.etree import ElementTree


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
manifest = ElementTree.parse(os.path.join(ROOT, "package.xml")).findtext("version")
marker = re.compile(r"<!-- epqrd_go2_bridge_VERSION: ([0-9]+\.[0-9]+\.[0-9]+) -->")
for name in ("README.md", "CHANGELOG.md"):
    with open(os.path.join(ROOT, name), "r", encoding="utf-8") as stream:
        match = marker.search(stream.read())
    if match is None or match.group(1) != manifest:
        raise SystemExit("%s version does not match package.xml" % name)
print("epqrd_go2_bridge version %s is consistent" % manifest)
