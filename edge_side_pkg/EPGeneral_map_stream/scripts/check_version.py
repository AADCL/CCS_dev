#!/usr/bin/env python3
import os
import re
import sys
from xml.etree import ElementTree


PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PACKAGE_ROOT, "src"))

from epgeneral_map_stream import __version__


def main():
    package_xml = os.path.join(PACKAGE_ROOT, "package.xml")
    manifest = ElementTree.parse(package_xml).getroot().findtext("version")
    failures = []
    if manifest != __version__:
        failures.append("Python version %s does not match package.xml %s" % (__version__, manifest))
    marker = re.compile(r"<!-- epgeneral_map_stream_VERSION: ([0-9]+\.[0-9]+\.[0-9]+) -->")
    for name in ("README.md", "CHANGELOG.md"):
        with open(os.path.join(PACKAGE_ROOT, name), "r", encoding="utf-8") as stream:
            match = marker.search(stream.read())
        if match is None or match.group(1) != manifest:
            failures.append("%s version marker does not match %s" % (name, manifest))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("epgeneral_map_stream version %s is consistent" % manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
