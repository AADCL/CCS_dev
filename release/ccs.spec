# Build on each target OS; mutable data is added outside _internal afterwards.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
datas = [
    (str(root / "icons/app_icons"), "icons/app_icons"),
    (str(root / "icons/lab_logo"), "icons/lab_logo"),
    (str(root / "ccs_monitor/assets"), "ccs_monitor/assets"),
]
if os.name != "nt":
    font = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if font.is_file():
        datas.append((str(font), "ccs_monitor/assets/fonts"))
        datas.append(("/usr/share/doc/fonts-noto-cjk/copyright", "licenses/fonts"))
binaries = []
hidden = ["amqtt.plugins.authentication", "vispy.app.backends._pyside6"]
for package in ("vispy", "open3d"):
    package_data, package_bins, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_bins
    hidden += package_hidden
for package in ("amqtt", "paho-mqtt", "zeroconf", "numpy", "pypcd4", "PySide6"):
    datas += copy_metadata(package, recursive=True)
entries = ["run", "fusion_worker_entry", "maintenance_entry"]
a = Analysis(
    [str(root / "run.py"), str(root / "scripts/fusion_worker_entry.py"), str(root / "scripts/maintenance_entry.py")],
    pathex=[str(root)], binaries=binaries, datas=datas, hiddenimports=hidden,
    excludes=["IPython", "pytest", "tkinter"], noarchive=False,
)
pyz = PYZ(a.pure)
bootstrap = [item for item in a.scripts if item[0] not in entries]
def executable(entry, name, console):
    scripts = bootstrap + [item for item in a.scripts if item[0] == entry]
    assert len(scripts) == len(bootstrap) + 1, (entry, a.scripts)
    return EXE(pyz, scripts, [], exclude_binaries=True, name=name, console=console,
               debug=False, strip=False, upx=False,
               icon=os.environ.get("CCS_APP_ICON") if name == "CCS" else None)
gui = executable("run", "CCS", os.name != "nt")
worker = executable("fusion_worker_entry", "ccs-map-fusion-worker", True)
maintenance = executable("maintenance_entry", "ccs-maintenance", True)
coll = COLLECT(gui, worker, maintenance, a.binaries, a.datas, strip=False, upx=False, name="CCS")
