"""Read-only Google Drive photo browsing and download helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from requests.exceptions import RequestException

from .imaging import SUPPORTED_EXTS
from .vault import delete_secret, get_secret, set_secret

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_TOKEN_SECRET = "google_drive_oauth"
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class DriveFile:
    """A Google Drive folder or supported image."""

    file_id: str
    name: str
    mime_type: str
    is_folder: bool


class GoogleDriveError(RuntimeError):
    """A user-actionable Google Drive integration error."""


def validate_client_secrets(path: Path) -> None:
    """Ensure the selected OAuth file is a Google Desktop client config."""
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    installed = value.get("installed") if isinstance(value, dict) else None
    if not isinstance(installed, dict) or not all(
        installed.get(field)
        for field in ("client_id", "client_secret", "auth_uri", "token_uri")
    ):
        raise ValueError(
            "Choose a Google OAuth client JSON file created for a Desktop app."
        )


def _authorized_credentials(client_secrets_path: Path) -> Credentials:
    token_json = get_secret(DRIVE_TOKEN_SECRET)
    credentials: Credentials | None = None
    if token_json:
        token_data = json.loads(token_json)
        if not isinstance(token_data, dict):
            raise ValueError("The saved Google Drive authorization is invalid.")
        credentials = Credentials.from_authorized_user_info(
            token_data, [DRIVE_READONLY_SCOPE]
        )
        if not credentials.has_scopes([DRIVE_READONLY_SCOPE]):
            credentials = None
        elif credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError:
                delete_secret(DRIVE_TOKEN_SECRET)
                credentials = None
        elif not credentials.valid:
            credentials = None

    if credentials is None:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secrets_path), [DRIVE_READONLY_SCOPE]
        )
        credentials = flow.run_local_server(port=0, open_browser=True)

    set_secret(DRIVE_TOKEN_SECRET, credentials.to_json())
    return credentials


def _drive_service(client_secrets_path: Path) -> Any:
    credentials = _authorized_credentials(client_secrets_path)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_drive_items(
    client_secrets_path: Path,
    folder_id: str,
) -> list[DriveFile]:
    """List child folders and supported image files in a Drive folder."""
    try:
        service = _drive_service(client_secrets_path)
        escaped_folder_id = folder_id.replace("\\", "\\\\").replace("'", "\\'")
        query = f"'{escaped_folder_id}' in parents and trashed = false"
        files: list[DriveFile] = []
        page_token: str | None = None
        while True:
            response = (
                service.files()
                .list(
                    q=query,
                    pageSize=1000,
                    pageToken=page_token,
                    orderBy="name",
                    fields="nextPageToken, files(id, name, mimeType)",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute()
            )
            for item in response.get("files", []):
                mime_type = str(item.get("mimeType", ""))
                name = str(item.get("name", ""))
                is_folder = mime_type == DRIVE_FOLDER_MIME_TYPE
                if is_folder or Path(name).suffix.lower() in SUPPORTED_EXTS:
                    files.append(
                        DriveFile(
                            file_id=str(item["id"]),
                            name=name,
                            mime_type=mime_type,
                            is_folder=is_folder,
                        )
                    )
            page_token = response.get("nextPageToken")
            if not page_token:
                return files
    except (
        GoogleAuthError,
        HttpError,
        RequestException,
        OSError,
        ValueError,
        RuntimeError,
    ) as exc:
        raise GoogleDriveError(f"Could not list Google Drive folder: {exc}") from exc


def _safe_filename(name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return sanitized or "drive-image"


def download_drive_images(
    client_secrets_path: Path,
    files: list[DriveFile],
    destination: Path,
) -> list[Path]:
    """Download selected images to a local import cache and return their paths."""
    images = [item for item in files if not item.is_folder]
    if not images:
        return []

    downloaded: list[Path] = []
    try:
        service = _drive_service(client_secrets_path)
        destination.mkdir(parents=True, exist_ok=True)
        for image in images:
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", image.file_id)
            image_directory = destination / (safe_id or "drive-image")
            image_directory.mkdir(parents=True, exist_ok=True)
            output = image_directory / _safe_filename(image.name)
            temporary = output.with_name(f"{output.name}.part")
            try:
                request = service.files().get_media(fileId=image.file_id)
                with temporary.open("wb") as file:
                    downloader = MediaIoBaseDownload(file, request)
                    complete = False
                    while not complete:
                        _, complete = downloader.next_chunk()
                temporary.replace(output)
            except (
                OSError,
                HttpError,
                GoogleAuthError,
                RequestException,
                RuntimeError,
            ):
                temporary.unlink(missing_ok=True)
                raise
            downloaded.append(output)
        return downloaded
    except (
        GoogleAuthError,
        HttpError,
        RequestException,
        OSError,
        ValueError,
        RuntimeError,
    ) as exc:
        raise GoogleDriveError(f"Could not download Google Drive photos: {exc}") from exc
