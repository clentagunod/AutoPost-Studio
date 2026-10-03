"""Local persistence for preferences, activity, and rotating application logs."""

from __future__ import annotations

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from . import config

ACTIVITY_PATH = config.ACTIVITY_PATH
APP_DIR = config.APP_DIR
DATA_DIR = config.DATA_DIR
LOG_DIR = config.LOG_DIR
OUTPUT_DIR = config.OUTPUT_DIR
PREFERENCES_PATH = config.PREFERENCES_PATH
PROJECT_ROOT = config.PROJECT_ROOT


def configure_logging() -> None:
    """Configure console and rotating file logs once per process."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("frame_studio")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = RotatingFileHandler(
        LOG_DIR / "frame_studio.log",
        maxBytes=512_000,
        backupCount=4,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)


def load_json(path: Path, fallback: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return fallback
    except (OSError, json.JSONDecodeError) as exc:
        logging.getLogger("frame_studio").warning(
            "Could not load JSON from %s; using defaults: %s", path, exc
        )
        return fallback


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(value, file, indent=2, ensure_ascii=True)
        file.write("\n")
    temporary_path.replace(path)


def load_preferences() -> dict[str, Any]:
    value = load_json(PREFERENCES_PATH, {})
    return value if isinstance(value, dict) else {}


def load_activity() -> list[dict[str, Any]]:
    value = load_json(ACTIVITY_PATH, [])
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)][-12:]


def record_activity(entry: dict[str, Any]) -> list[dict[str, Any]]:
    activity = load_activity()
    activity.append(entry)
    activity = activity[-12:]
    save_json(ACTIVITY_PATH, activity)
    return activity
