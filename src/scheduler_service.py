"""Detached Windows worker for locally queued Facebook Page posts."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from google import genai
from google.genai.errors import APIError
from groq import Groq, GroqError
from winotify import Notification

from .captions import PROVIDER_DEFAULTS
from .facebook import FacebookError, post_album, post_photo, validate_page_access
from .scheduler import PostRecord, load_posts, save_post
from .storage import (
    DATA_DIR,
    OUTPUT_DIR,
    configure_logging,
    load_preferences,
    save_json,
)
from .vault import get_secret

LOG = logging.getLogger("frame_studio.scheduler_service")
POLL_SECONDS = 15
CREDENTIAL_CHECK_SECONDS = 6 * 60 * 60
MUTEX_NAME = "Local\\AutoPostStudioScheduler"
STATE_PATH = DATA_DIR / "scheduler_service_state.json"


def notify(title: str, message: str) -> None:
    try:
        Notification(
            app_id="AutoPost Studio",
            title=title[:64],
            msg=message[:240],
        ).show()
    except Exception:
        LOG.exception("Could not display Windows notification")


def _scrub_secret(message: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message


def _save_record(record: PostRecord, folder: Path) -> None:
    save_post(record, folder)


def process_due_posts(
    folder: Path,
    now: datetime | None = None,
    token: str | None = None,
) -> int:
    """Publish all queued posts due at the supplied local time."""
    current = now or datetime.now()
    try:
        records = load_posts(folder)
    except (OSError, ValueError) as exc:
        LOG.exception("Could not load scheduled posts from %s", folder)
        notify("Scheduler could not read posts", str(exc))
        return 0

    due: list[PostRecord] = []
    for record in records:
        if record.status != "queued" or not record.scheduled_at:
            continue
        try:
            scheduled = datetime.strptime(record.scheduled_at, "%Y-%m-%d %H:%M")
        except ValueError:
            record.status = "failed"
            record.last_error = f"Invalid saved schedule: {record.scheduled_at}"
            try:
                _save_record(record, folder)
            except OSError:
                LOG.exception("Could not save invalid-schedule error for %s", record.id)
            notify("Scheduled post needs attention", record.last_error)
            continue
        if scheduled <= current:
            due.append(record)

    groups: list[list[PostRecord]] = []
    grouped: dict[str, list[PostRecord]] = {}
    for record in due:
        if record.group_id:
            grouped.setdefault(record.group_id, []).append(record)
        else:
            groups.append([record])
    groups.extend(grouped.values())

    secret = token
    for group in groups:
        published_to_facebook = False
        try:
            for record in group:
                record.status = "processing"
                record.last_error = ""
                _save_record(record, folder)
            if not secret:
                secret = get_secret("facebook_page_token")
            if not secret:
                raise FacebookError(
                    "Facebook Page access token is missing. Save a valid token in Options → APIs."
                )
            if group[0].group_id:
                if not 2 <= len(group) <= 10:
                    raise ValueError(
                        "A queued album must contain between 2 and 10 images."
                    )
                if len({record.page_id for record in group}) != 1:
                    raise ValueError("A queued album contains multiple Page IDs.")
                result = post_album(
                    group[0].page_id,
                    secret,
                    [record.image for record in group],
                    group[0].caption,
                )
                remote_id = str(result.get("id") or "")
            else:
                result = post_photo(
                    group[0].page_id,
                    secret,
                    group[0].image,
                    group[0].caption,
                )
                remote_id = str(result.get("post_id") or result.get("id") or "")
            published_to_facebook = True
            for record in group:
                record.status = "published"
                record.remote_id = remote_id
                record.last_error = ""
                _save_record(record, folder)
            notify(
                "Facebook post published",
                f"{len(group)} post(s) published successfully. ID: {remote_id or 'not returned'}",
            )
        except (FacebookError, OSError, ValueError, RuntimeError) as exc:
            message = _scrub_secret(str(exc), [secret or ""])
            LOG.error("Scheduled post failed: %s", message)
            if published_to_facebook:
                notify(
                    "Facebook accepted post; local status needs review",
                    "Facebook accepted the post, but AutoPost Studio could not save its final local status. "
                    "Check the Page before retrying to avoid duplicates. " + message,
                )
                continue
            for record in group:
                record.status = "failed"
                record.last_error = message
                try:
                    _save_record(record, folder)
                except OSError:
                    LOG.exception("Could not persist scheduler failure for %s", record.id)
            title = (
                "Facebook token needs attention"
                if any(
                    word in message.lower()
                    for word in ("token", "session is invalid", "logged out", "oauth")
                )
                else "Scheduled post failed"
            )
            notify(title, message)
    return len(due)


def _probe_provider(provider: str, endpoint: str, api_key: str) -> bool | None:
    """Return False only for clear authentication rejection; None means inconclusive."""
    try:
        if provider == "groq":
            client = Groq(api_key=api_key, timeout=20.0)
            try:
                client.models.list()
            finally:
                client.close()
            return True
        if provider == "gemini":
            client = genai.Client(api_key=api_key, http_options={"timeout": 20000})
            try:
                next(iter(client.models.list()), None)
            finally:
                client.close()
            return True
        if provider == "openrouter":
            response = requests.get(
                f"{endpoint.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=(5, 15),
            )
            if response.status_code in {401, 403}:
                return False
            return True if response.ok else None
    except GroqError as exc:
        return False if getattr(exc, "status_code", None) in {401, 403} else None
    except APIError as exc:
        code = getattr(exc, "code", None)
        message = str(exc).lower()
        return (
            False
            if code in {401, 403}
            or "api key not valid" in message
            or "invalid api key" in message
            else None
        )
    except requests.RequestException:
        return None
    except Exception:
        LOG.exception("Could not validate %s API credentials", provider)
        return None
    return None


def check_credentials() -> None:
    preferences = load_preferences()
    state: dict[str, Any] = {}
    try:
        state_path = STATE_PATH
        if state_path.is_file():
            import json

            with state_path.open("r", encoding="utf-8") as file:
                value: Any = json.load(file)
            if isinstance(value, dict):
                state = value
    except (OSError, ValueError):
        LOG.exception("Could not read scheduler service notification state")

    invalid = set(state.get("invalid_credentials", []))
    page_id = str(preferences.get("facebook_page_id", "")).strip()
    try:
        page_token = get_secret("facebook_page_token")
    except RuntimeError:
        LOG.exception("Could not access Facebook token from credential vault")
        page_token = ""
    if page_id and page_token:
        try:
            validate_page_access(page_id, page_token)
            invalid.discard("facebook")
        except FacebookError as exc:
            message = str(exc).lower()
            if any(word in message for word in ("token", "session", "logged out", "oauth")):
                if "facebook" not in invalid:
                    notify("Facebook token needs attention", str(exc))
                invalid.add("facebook")

    for provider, defaults in PROVIDER_DEFAULTS.items():
        if not defaults["requires_api_key"]:
            continue
        try:
            api_key = get_secret(f"caption_api_key_{provider}")
            if not api_key and provider == "groq":
                api_key = get_secret("caption_api_key")
        except RuntimeError:
            LOG.exception("Could not access caption credential from vault")
            api_key = ""
        if api_key:
            endpoints = preferences.get("caption_endpoints", {})
            endpoint = (
                str(endpoints.get(provider, defaults["endpoint"]))
                if isinstance(endpoints, dict)
                else str(defaults["endpoint"])
            )
            valid = _probe_provider(provider, endpoint, api_key)
            key_id = f"caption:{provider}"
            if valid is False:
                if key_id not in invalid:
                    notify(
                        f"{defaults['label']} key needs attention",
                        "The provider rejected the saved API key. Check or replace it in Options → Captions.",
                    )
                invalid.add(key_id)
            elif valid is True:
                invalid.discard(key_id)
    try:
        save_json(STATE_PATH, {"invalid_credentials": sorted(invalid)})
    except OSError:
        LOG.exception("Could not save scheduler service credential state")


def _recover_interrupted_posts(folder: Path) -> None:
    try:
        records = load_posts(folder)
    except (OSError, ValueError):
        LOG.exception("Could not check for interrupted posts")
        return
    for record in records:
        if record.status == "processing":
            record.status = "failed"
            record.last_error = (
                "The background worker stopped while this post was being sent. "
                "Check the Page before retrying to avoid duplicate posts."
            )
            try:
                save_post(record, folder)
                notify("Post status needs review", record.last_error)
            except OSError:
                LOG.exception("Could not recover interrupted post %s", record.id)


def run_scheduler_service() -> None:
    """Run until stopped externally (for example, from Windows Task Manager)."""
    configure_logging()
    mutex = None
    kernel32 = None
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            LOG.info("Scheduler background worker is already running")
            kernel32.CloseHandle(mutex)
            return
    LOG.info("Scheduler background worker started (pid=%s)", os.getpid())
    try:
        _recover_interrupted_posts(
            Path(load_preferences().get("scheduler_dir") or OUTPUT_DIR).expanduser()
        )
    except Exception:
        LOG.exception("Could not recover interrupted posts at startup")
        notify(
            "Scheduler startup needs attention",
            "The scheduler could not recover interrupted posts. Check the application log.",
        )
    last_credential_check = 0.0
    cycle_error_active = False
    try:
        while True:
            try:
                preferences = load_preferences()
                folder = Path(
                    preferences.get("scheduler_dir") or OUTPUT_DIR
                ).expanduser()
                process_due_posts(folder)
                now = time.monotonic()
                if now - last_credential_check >= CREDENTIAL_CHECK_SECONDS:
                    check_credentials()
                    last_credential_check = now
                cycle_error_active = False
            except Exception:
                LOG.exception("Scheduler worker cycle failed; it will retry")
                if not cycle_error_active:
                    notify(
                        "Scheduler needs attention",
                        "A background scheduler check failed. It will retry; see the application log for details.",
                    )
                cycle_error_active = True
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        LOG.info("Scheduler worker interrupted")
    finally:
        if mutex is not None and kernel32 is not None:
            kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    run_scheduler_service()
