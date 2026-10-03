"""Tests for local persistence and application logging."""

from __future__ import annotations

import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

from src import storage


class StorageTests(unittest.TestCase):
    def test_missing_json_file_uses_fallback_without_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing_path = Path(temporary) / "preferences.json"
            logger = logging.getLogger("frame_studio")
            with self.assertNoLogs(logger, level="WARNING"):
                value = storage.load_json(missing_path, {})

        self.assertEqual(value, {})

    def test_logging_works_without_stderr(self) -> None:
        logger = logging.getLogger("frame_studio")
        original_handlers = logger.handlers[:]
        added_handlers: list[logging.Handler] = []
        logger.handlers.clear()
        with tempfile.TemporaryDirectory() as temporary:
            try:
                root = Path(temporary)
                with (
                    patch.object(storage, "DATA_DIR", root / "data"),
                    patch.object(storage, "LOG_DIR", root / "logs"),
                    patch.object(storage, "OUTPUT_DIR", root / "outputs"),
                    patch.object(storage.sys, "stderr", None),
                ):
                    storage.configure_logging()
                    added_handlers = logger.handlers[:]

                self.assertEqual(len(added_handlers), 1)
                self.assertIsInstance(added_handlers[0], RotatingFileHandler)
            finally:
                for handler in added_handlers:
                    logger.removeHandler(handler)
                    handler.close()
                logger.handlers[:] = original_handlers


if __name__ == "__main__":
    unittest.main()
