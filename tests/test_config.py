"""Tests for app configuration and resource lookup."""

from __future__ import annotations

import unittest

from src.config import (
    APP_DIR,
    APP_NAME,
    APP_VERSION,
    DATA_DIR,
    DEFAULT_FRAME_PATH,
    ICON_PATH,
    LOG_DIR,
    OUTPUT_DIR,
    RESOURCE_ROOT,
    resource_path,
)


class ApplicationConfigTests(unittest.TestCase):
    def test_identity_and_runtime_directories_are_configured(self) -> None:
        self.assertEqual(APP_NAME, "AutoPost Studio")
        self.assertRegex(APP_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(DATA_DIR, APP_DIR / "data")
        self.assertEqual(LOG_DIR, APP_DIR / "logs")
        self.assertEqual(OUTPUT_DIR, APP_DIR / "outputs")

    def test_asset_paths_resolve_from_resource_root(self) -> None:
        self.assertEqual(ICON_PATH, resource_path("app_icon.ico"))
        self.assertEqual(DEFAULT_FRAME_PATH, resource_path("frame_sample.png"))
        self.assertTrue(ICON_PATH.is_file())
        self.assertTrue(DEFAULT_FRAME_PATH.is_file())
        self.assertEqual(ICON_PATH.parent, RESOURCE_ROOT)


if __name__ == "__main__":
    unittest.main()
