#!/usr/bin/python3
"""Verify the epgeneral_mqtav manifest and version-marked documents agree."""

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from epgeneral_mqtav.version import get_version  # noqa: E402


DOCUMENTS = ("README.md", "DEVELOPMENT_LOG.md", "CHANGELOG.md")
MARKER = re.compile(r"<!-- epgeneral_mqtav_VERSION: ([0-9]+\.[0-9]+\.[0-9]+) -->")


def main():
    version = get_version(ROOT / "package.xml")
    failures = []
    for name in DOCUMENTS:
        path = ROOT / name
        match = MARKER.search(path.read_text(encoding="utf-8"))
        if match is None:
            failures.append(f"{name}: missing epgeneral_mqtav_VERSION marker")
        elif match.group(1) != version:
            failures.append(f"{name}: {match.group(1)} does not match package.xml {version}")
    if failures:
        print("Version consistency check failed:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"epgeneral_mqtav version {version} is consistent across manifest and documentation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
