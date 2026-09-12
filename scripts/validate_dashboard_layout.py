"""Run the command-dashboard increment in isolated processes and Qt settings."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

JOBS = (
    'tests.test_command_dashboard',
    'tests.test_dashboard_layout',
    'tests.test_theme_v010',
    'tests.test_app_icons',
    'tests.test_point_cloud',
    'tests.test_pgm_map',
    'tests.test_task_protocol_service',
    'tests.test_map_picking',
    'tests.test_map_device_overlay',
    'tests.test_multi_device_v024.TrajectoryViewerTests',
    'tests.test_ui.UiTests.test_navigation_contains_five_pages_and_defaults_home',
    'tests.test_ui.UiTests.test_theme_button_switches_theme_without_changing_page',
    'tests.test_ui.UiTests.test_lab_branding_follows_version_and_fits_narrow_navigation',
)


def run_job(name: str) -> int:
    from PySide6 import QtCore
    original = QtCore.QSettings
    with tempfile.TemporaryDirectory(prefix='ccs-dashboard-settings-') as directory:
        class IsolatedSettings(original):
            def __init__(self, *args, **kwargs):
                # Explicit file-backed settings already belong to test fixtures.
                if not args or (isinstance(args[0], str) and
                                (len(args) == 1 or isinstance(args[1], str))):
                    super().__init__(str(Path(directory) / 'settings.ini'), original.Format.IniFormat)
                else:
                    super().__init__(*args, **kwargs)

        QtCore.QSettings = IsolatedSettings
        try:
            suite = unittest.defaultTestLoader.loadTestsFromName(name)
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() and not result.skipped else 1
        finally:
            QtCore.QSettings = original


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('build/dashboard-validation'))
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--job', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.job:
        return run_job(args.job)
    args.output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONIOENCODING='utf-8')
    results = []
    for job in JOBS:
        try:
            run = subprocess.run(
                [sys.executable, '-X', 'faulthandler', str(Path(__file__).resolve()), '--job', job],
                cwd=Path(__file__).resolve().parents[1], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=args.timeout,
            )
            output = run.stdout.decode('utf-8', errors='replace')
            code = run.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b'').decode('utf-8', errors='replace') + '\nTIMEOUT\n'
            code = -1
        (args.output / f'{job}.log').write_text(output, encoding='utf-8')
        count = re.search(r'Ran (\d+) tests?', output)
        result = dict(job=job, exit_code=code, tests=int(count[1]) if count else None,
                      skipped=re.findall(r'skipped[^\n]*', output))
        results.append(result)
        print(json.dumps(result), flush=True)
        (args.output / 'summary.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    return int(any(item['exit_code'] for item in results))


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
