"""Application-owned files always travel with the installation."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import tempfile


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_root() -> Path:
    return Path(sys.executable).resolve().parent if is_frozen() else Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", application_root())) if is_frozen() else application_root()


def prepare_storage() -> None:
    """Probe real writes, not just permission bits (ACLs can override them)."""
    for relative in ("config", "data", "data/logs"):
        directory = application_root() / relative
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory):
                pass
        except OSError as exc:
            raise PermissionError(f"软件目录不可写：{directory}\n请将 CCS 安装或移动到当前用户可写的目录。") from exc


def configure_logging() -> None:
    target = application_root() / "data" / "logs" / "ccs.log"
    handler = RotatingFileHandler(target, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def fusion_worker_command(request: Path, result: Path) -> list[str]:
    if is_frozen():
        filename = "ccs-map-fusion-worker.exe" if sys.platform == "win32" else "ccs-map-fusion-worker"
        return [str(application_root() / filename), str(request), str(result)]
    return [sys.executable, "-m", "ccs_monitor.map_fusion_worker", str(request), str(result)]
