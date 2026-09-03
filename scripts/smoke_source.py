"""Run the real source entry point from an extracted portable directory."""
import argparse
import os
from pathlib import Path
import runpy
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
args = parser.parse_args()
root = args.directory.resolve()
os.chdir(root)
sys.path.insert(0, str(root))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ccs_monitor.runtime_paths import application_root
import ccs_monitor.app as application

assert application_root() == root
for cls in (application.NtpServerService, application.MqttMonitoringRuntime,
            application.UdpMonitoringRuntime, application.MapBuildingService,
            application.RelocalizationService, application.TaskExecutionService,
            application.SrtCapabilityProbe):
    cls.start = lambda *args, **kwargs: None
original_show = application.MainWindow.show
def show(window):
    original_show(window)
    QTimer.singleShot(300, QApplication.instance().quit)
application.MainWindow.show = show
try:
    runpy.run_path(str(root / "run.py"), run_name="__main__")
except SystemExit as exc:
    if exc.code:
        raise
print(f"PASS: portable run.py starts, application root = {root}")
