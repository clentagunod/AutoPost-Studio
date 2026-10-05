"""Application identity, resource lookup, and writable data locations."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

APP_NAME = "AutoPost Studio"
APP_VERSION = "2.1.1"
LOGGER_NAME = "frame_studio"
ICON_RESOURCE = "app_icon.ico"
DEFAULT_FRAME_RESOURCE = Path("frame_sample.png")
GCASH_QR_RESOURCE = Path("qr_code.jpg")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IS_FROZEN = bool(vars(sys).get("frozen", False))
RESOURCE_ROOT = Path(str(vars(sys).get("_MEIPASS", PROJECT_ROOT)))
APP_INSTALL_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else PROJECT_ROOT


def _resolve_app_data_dir(
    is_frozen: bool,
    project_root: Path,
    platform_name: str,
    home: Path,
    environ: Mapping[str, str],
) -> Path:
    """Return a writable per-user directory for packaged-app data."""
    if not is_frozen:
        return project_root

    if platform_name == "win32":
        base_dir = Path(environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
    elif platform_name == "darwin":
        base_dir = home / "Library" / "Application Support"
    else:
        base_dir = Path(environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return base_dir / APP_NAME


APP_DIR = _resolve_app_data_dir(
    IS_FROZEN,
    PROJECT_ROOT,
    sys.platform,
    Path.home(),
    os.environ,
)

DATA_DIR = APP_DIR / "data"
LOG_DIR = APP_DIR / "logs"
OUTPUT_DIR = APP_DIR / "outputs"
PREFERENCES_PATH = DATA_DIR / "preferences.json"
ACTIVITY_PATH = DATA_DIR / "recent_activity.json"


def _resolve_icon_path(app_dir: Path, resource_root: Path, frozen: bool) -> Path:
    """Prefer an icon beside a packaged executable, then use its bundled copy."""
    companion_icon = app_dir / ICON_RESOURCE
    if frozen and companion_icon.is_file():
        return companion_icon
    return resource_root / ICON_RESOURCE


ICON_PATH = _resolve_icon_path(APP_INSTALL_DIR, RESOURCE_ROOT, IS_FROZEN)
DEFAULT_FRAME_PATH = RESOURCE_ROOT / DEFAULT_FRAME_RESOURCE
GCASH_QR_PATH = RESOURCE_ROOT / GCASH_QR_RESOURCE


def resource_path(*parts: str) -> Path:
    """Return a path to a resource in the source tree or PyInstaller bundle."""
    return RESOURCE_ROOT.joinpath(*parts)
