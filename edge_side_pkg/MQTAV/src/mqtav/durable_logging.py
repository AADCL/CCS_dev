"""Rotating file logging that synchronizes each record to persistent storage."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


class DurableRotatingFileHandler(RotatingFileHandler):
    """Flush and fsync after every record, including process-failure diagnostics."""

    def emit(self, record):
        super().emit(record)
        if self.stream is not None:
            self.stream.flush()
            os.fsync(self.stream.fileno())


def build_logger(log_dir=None):
    path = Path(log_dir).expanduser() if log_dir else Path("~/.ros/log/mqtav").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mqtav")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = DurableRotatingFileHandler(
            path / "mqtav.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [mqtav] %(message)s"))
        logger.addHandler(console)
    return logger
