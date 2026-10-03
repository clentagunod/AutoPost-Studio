"""Application identity, resource lookup, and writable data locations."""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "AutoPost Studio"
APP_VERSION = "1.1.5"
LOGGER_NAME = "frame_studio"
ICON_RESOURCE = "app_icon.ico"
DEFAULT_FRAME_RESOURCE = Path("frame_sample.png")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IS_FROZEN = bool(vars(sys).get("frozen", False))
RESOURCE_ROOT = Path(str(vars(sys).get("_MEIPASS", PROJECT_ROOT)))
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else PROJECT_ROOT

DATA_DIR = APP_DIR / "data"
LOG_DIR = APP_DIR / "logs"
OUTPUT_DIR = APP_DIR / "outputs"
PREFERENCES_PATH = DATA_DIR / "preferences.json"
ACTIVITY_PATH = DATA_DIR / "recent_activity.json"
ICON_PATH = RESOURCE_ROOT / ICON_RESOURCE
DEFAULT_FRAME_PATH = RESOURCE_ROOT / DEFAULT_FRAME_RESOURCE


def resource_path(*parts: str) -> Path:
    """Return a path to a resource in the source tree or PyInstaller bundle."""
    return RESOURCE_ROOT.joinpath(*parts)
