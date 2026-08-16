import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mqtav.durable_logging import DurableRotatingFileHandler


class LoggingTests(unittest.TestCase):
    def test_handler_flushes_and_fsyncs_each_record(self):
        with tempfile.TemporaryDirectory() as directory:
            handler = DurableRotatingFileHandler(Path(directory) / "mqtav.log", maxBytes=1024, backupCount=1)
            logger = logging.getLogger("mqtav_test_durable")
            logger.handlers.clear()
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            with patch("mqtav.durable_logging.os.fsync") as fsync:
                logger.info("connection lost")
                self.assertTrue(fsync.called)
            handler.close()
            self.assertIn("connection lost", (Path(directory) / "mqtav.log").read_text(encoding="utf-8"))

    def test_build_logger_adds_console_handler(self):
        from mqtav.durable_logging import build_logger

        with tempfile.TemporaryDirectory() as directory:
            logger = logging.getLogger("mqtav")
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
            logger = build_logger(directory)
            self.assertTrue(any(isinstance(handler, logging.StreamHandler) and not hasattr(handler, "baseFilename")
                                for handler in logger.handlers))
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
