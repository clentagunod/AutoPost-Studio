"""Tests for app configuration and resource lookup."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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
    _resolve_app_data_dir,
    _resolve_icon_path,
    resource_path,
)


class ApplicationConfigTests(unittest.TestCase):
    def test_identity_and_runtime_directories_are_configured(self) -> None:
        self.assertEqual(APP_NAME, "AutoPost Studio")
        self.assertRegex(APP_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(DATA_DIR, APP_DIR / "data")
        self.assertEqual(LOG_DIR, APP_DIR / "logs")
        self.assertEqual(OUTPUT_DIR, APP_DIR / "outputs")

    def test_packaged_data_directory_uses_local_app_data(self) -> None:
        home = Path("C:/Users/tester")
        project_root = Path("C:/project")
        local_app_data = Path("C:/Users/tester/AppData/Local")

        self.assertEqual(
            _resolve_app_data_dir(
                True,
                project_root,
                "win32",
                home,
                {"LOCALAPPDATA": str(local_app_data)},
            ),
            local_app_data / APP_NAME,
        )
        self.assertEqual(
            _resolve_app_data_dir(False, project_root, "win32", home, {}),
            project_root,
        )

    def test_asset_paths_resolve_from_resource_root(self) -> None:
        self.assertEqual(ICON_PATH, resource_path("app_icon.ico"))
        self.assertEqual(DEFAULT_FRAME_PATH, resource_path("frame_sample.png"))
        self.assertTrue(ICON_PATH.is_file())
        self.assertTrue(DEFAULT_FRAME_PATH.is_file())
        self.assertEqual(ICON_PATH.parent, RESOURCE_ROOT)

    def test_packaged_icon_prefers_file_beside_executable(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            install_dir = root / "app"
            resource_root = root / "bundle"
            install_dir.mkdir()
            resource_root.mkdir()
            companion_icon = install_dir / "app_icon.ico"
            companion_icon.touch()
            (resource_root / "app_icon.ico").touch()

            self.assertEqual(
                _resolve_icon_path(install_dir, resource_root, frozen=True),
                companion_icon,
            )

    def test_packaged_icon_falls_back_to_bundled_file(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            install_dir = root / "app"
            resource_root = root / "bundle"
            install_dir.mkdir()
            resource_root.mkdir()
            bundled_icon = resource_root / "app_icon.ico"
            bundled_icon.touch()

            self.assertEqual(
                _resolve_icon_path(install_dir, resource_root, frozen=True),
                bundled_icon,
            )

    def test_source_icon_uses_resource_root(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            install_dir = root / "app"
            resource_root = root / "bundle"
            install_dir.mkdir()
            resource_root.mkdir()
            (install_dir / "app_icon.ico").touch()
            bundled_icon = resource_root / "app_icon.ico"
            bundled_icon.touch()

            self.assertEqual(
                _resolve_icon_path(install_dir, resource_root, frozen=False),
                bundled_icon,
            )


if __name__ == "__main__":
    unittest.main()
