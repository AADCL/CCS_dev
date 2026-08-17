import argparse
import json
import os
import sys

from .config import ConfigError
from .fusion import FusionError, run_fusion


def default_config_path():
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "config", "fusion.yaml")
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fuse two or more PCD maps using measured placements")
    parser.add_argument("--config-file", default=default_config_path())
    parser.add_argument("--job-file", required=True)
    arguments = parser.parse_args(argv)
    try:
        report = run_fusion(arguments.config_file, arguments.job_file)
    except (ConfigError, FusionError) as exc:
        sys.stderr.write("epgeneral_multi_map_fusion: %s\n" % exc)
        return 2
    sys.stdout.write(
        json.dumps(
            {
                "output": report["output"]["pcd_path"],
                "point_count": report["output"]["point_count"],
                "reference_map_id": report["reference_map_id"],
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
