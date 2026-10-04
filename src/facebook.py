"""Facebook Page photo publishing through Meta's Graph API."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

GRAPH_VERSION = os.getenv("FB_GRAPH_VERSION", "v26.0")
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
MAX_CAPTION = 63_206
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class FacebookError(RuntimeError):
    """Raised when Meta Graph API rejects or cannot process a post."""


def validate_page_access(page_id: str, token: str) -> str:
    """Confirm a saved token can read the configured Page, without posting."""
    if not page_id.strip() or not token.strip():
        raise ValueError("Enter both the Facebook Page ID and access token.")
    try:
        response = requests.get(
            f"{GRAPH_URL}/{page_id}",
            params={"fields": "id,name", "access_token": token},
            timeout=(10, 20),
        )
    except requests.RequestException as exc:
        raise FacebookError(f"Could not reach Meta Graph API: {exc}") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise FacebookError("Meta Graph API returned an invalid response.") from exc
    if not isinstance(body, dict):
        raise FacebookError("Meta Graph API returned an unexpected response.")
    error = body.get("error")
    if not response.ok or error:
        if isinstance(error, dict):
            message = str(error.get("message", "Page access check failed."))
            code = error.get("code")
            if code == 200 and "publish_actions" in message.lower():
                message = _explain_deprecated_publish_permission(message)
        else:
            message = "Page access check failed."
        raise FacebookError(message)
    if str(body.get("id", "")) != page_id:
        raise FacebookError("Meta returned a different Page than the configured Page ID.")
    return str(body.get("name") or page_id)


def post_photo(
    page_id: str,
    token: str,
    image: str,
    caption: str = "",
    schedule_at: datetime | None = None,
) -> dict[str, Any]:
    if not page_id.strip() or not token.strip():
        raise ValueError("A Facebook Page ID and Page access token are required.")
    if len(caption) > MAX_CAPTION:
        raise ValueError("Caption exceeds Facebook's post text limit.")
    data: dict[str, Any] = {"caption": caption}
    if schedule_at:
        delta = schedule_at.timestamp() - time.time()
        if not 10 * 60 <= delta <= 30 * 24 * 3600:
            raise ValueError("Facebook scheduled posts must be 10 minutes to 30 days ahead.")
        data.update(published="false", scheduled_publish_time=int(schedule_at.timestamp()))
    endpoint = f"{page_id}/photos"
    if image.lower().startswith(("http://", "https://")):
        data["url"] = image
        return _post(endpoint, token, data)

    path = Path(image).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    with tempfile.TemporaryDirectory(prefix="autopost-facebook-") as temporary:
        upload = Path(temporary) / "facebook-ready.jpg"
        _prepare_upload(path, upload)
        if upload.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Facebook-ready image exceeds Facebook's 10 MB photo limit.")
        with upload.open("rb") as file:
            return _post(
                endpoint, token, data, {"source": (upload.name, file, "image/jpeg")}
            )


def post_album(
    page_id: str,
    token: str,
    images: list[str],
    caption: str = "",
    schedule_at: datetime | None = None,
) -> dict[str, Any]:
    """Publish a multi-photo Page post from 2-10 local images."""
    if not page_id.strip() or not token.strip():
        raise ValueError("A Facebook Page ID and Page access token are required.")
    if not 2 <= len(images) <= 10:
        raise ValueError("A Facebook album must contain between 2 and 10 images.")
    if len(caption) > MAX_CAPTION:
        raise ValueError("Caption exceeds Facebook's post text limit.")
    if schedule_at:
        delta = schedule_at.timestamp() - time.time()
        if not 10 * 60 <= delta <= 30 * 24 * 3600:
            raise ValueError(
                "Facebook scheduled posts must be 10 minutes to 30 days ahead."
            )

    paths = [Path(image).expanduser() for image in images]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {path}")

    uploaded_ids: list[str] = []
    with tempfile.TemporaryDirectory(prefix="autopost-album-") as temporary:
        for index, path in enumerate(paths):
            prepared = Path(temporary) / f"photo-{index:02d}.jpg"
            _prepare_upload(path, prepared)
            if prepared.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError(
                    f"Facebook-ready image exceeds Facebook's 10 MB photo limit: {path.name}"
                )
            data: dict[str, Any] = {"published": "false"}
            if schedule_at:
                data["temporary"] = "true"
            try:
                with prepared.open("rb") as file:
                    response = _post(
                        f"{page_id}/photos",
                        token,
                        data,
                        {"source": (prepared.name, file, "image/jpeg")},
                    )
            except FacebookError as exc:
                if uploaded_ids:
                    raise FacebookError(
                        f"Facebook accepted {len(uploaded_ids)} unpublished album "
                        "image(s), but the next upload failed. No album post was created; "
                        f"Facebook should remove unused uploads within about 24 hours. {exc}"
                    ) from exc
                raise
            photo_id = response.get("id")
            if not isinstance(photo_id, (str, int)) or not str(photo_id):
                raise FacebookError(
                    f"Facebook uploaded {len(uploaded_ids) + 1} album image(s) "
                    "but did not return a photo ID."
                )
            uploaded_ids.append(str(photo_id))

    feed_data: dict[str, Any] = {"message": caption}
    for index, photo_id in enumerate(uploaded_ids):
        feed_data[f"attached_media[{index}]"] = json.dumps(
            {"media_fbid": photo_id}, separators=(",", ":")
        )
    if schedule_at:
        feed_data.update(
            {
                "published": "false",
                "scheduled_publish_time": int(schedule_at.timestamp()),
                "unpublished_content_type": "SCHEDULED",
            }
        )
    try:
        return _post(f"{page_id}/feed", token, feed_data)
    except FacebookError as exc:
        raise FacebookError(
            f"Facebook accepted {len(uploaded_ids)} unpublished album image(s), "
            "but the album post failed. The images remain unpublished and Facebook "
            f"should remove them within about 24 hours. {exc}"
        ) from exc


def _prepare_upload(source: Path, destination: Path) -> None:
    """Optimize the complete source image without adding a canvas or cropping."""
    try:
        with Image.open(source) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Cannot read image for Facebook: {source}: {exc}") from exc

    image.save(destination, "JPEG", quality=92, optimize=True, progressive=True)


def _post(
    endpoint: str,
    token: str,
    data: dict[str, Any],
    files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    last_error = "Unknown Meta Graph API error"
    for attempt in range(3):
        try:
            if files:
                for _, (_, file, _) in files.items():
                    file.seek(0)
            response = requests.post(
                f"{GRAPH_URL}/{endpoint}",
                data={**data, "access_token": token},
                files=files,
                timeout=(10, 120),
            )
        except requests.RequestException as exc:
            last_error = f"Network error: {exc}"
            retryable = True
        else:
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                raise FacebookError("Meta Graph API returned an unexpected response.")
            if response.ok and "error" not in body:
                return body
            error = body.get("error", {})
            if not isinstance(error, dict):
                error = {}
            last_error = str(error.get("message", response.text[:300]))
            if error.get("code") == 200 and "publish_actions" in last_error.lower():
                raise FacebookError(_explain_deprecated_publish_permission(last_error))
            retryable = (
                error.get("code") in {1, 2, 4, 17, 32, 341, 613}
                or response.status_code >= 500
            )
            if not retryable:
                raise FacebookError(last_error)
        if attempt < 2 and retryable:
            time.sleep(2 ** (attempt + 1))
    raise FacebookError(f"Facebook request failed after retries: {last_error}")


def _explain_deprecated_publish_permission(message: str) -> str:
    return (
        "Meta rejected this token because it references the deprecated "
        "`publish_actions` permission. AutoPost Studio does not use that permission. "
        "Create a new System User token in Meta Business Settings, assign the System "
        "User both this app and the target Page with content-creation rights, and grant "
        "the current Pages API permissions such as `pages_manage_posts`. Do not request "
        "`publish_actions`; replace the saved token under Options → Facebook Pages. "
        f"Meta response: {message}"
    )
