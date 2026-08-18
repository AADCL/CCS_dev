#!/usr/bin/env python3
import os
import xml.etree.ElementTree as ET

from epgeneral_multi_map import __version__


def main():
    package = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    declared = ET.parse(os.path.join(package, "package.xml")).findtext("version")
    if declared != __version__:
        raise SystemExit("version mismatch: package.xml=%s python=%s" % (declared, __version__))
    print("epgeneral_multi_map version %s is consistent" % __version__)


if __name__ == "__main__":
    main()
