"""Tests for Google Drive photo browsing and local downloads."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QDialog

from src.app import AutoPostStudio
from unittest.mock import patch

from src.google_drive import (
    DRIVE_FOLDER_MIME_TYPE,
    DriveFile,
    download_drive_images,
    list_drive_items,
    validate_client_secrets,
)


class FakeRequest:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def execute(self) -> dict[str, object]:
        return self.response


class FakeDriveFiles:
    def __init__(self, pages: dict[str | None, dict[str, object]]) -> None:
        self.pages = pages
        self.list_calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> FakeRequest:
        self.list_calls.append(kwargs)
        token = kwargs.get("pageToken")
        return FakeRequest(self.pages[str(token) if token else None])

    def get_media(self, fileId: str) -> bytes:
        return f"downloaded {fileId}".encode()


class FakeDriveService:
    def __init__(self, pages: dict[str | None, dict[str, object]]) -> None:
        self.file_api = FakeDriveFiles(pages)

    def files(self) -> FakeDriveFiles:
        return self.file_api


class FakeDownloader:
    def __init__(self, file: object, request: bytes) -> None:
        self.file = file
        self.request = request
        self.finished = False

    def next_chunk(self) -> tuple[None, bool]:
        if not self.finished:
            self.file.write(self.request)
            self.finished = True
        return None, self.finished


class GoogleDriveTests(unittest.TestCase):
    def test_import_action_sends_downloaded_photos_to_batch_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            client_secrets = Path(temporary) / "client.json"
            client_secrets.touch()
            imported_paths = [Path(temporary) / "photo.jpg"]
            owner = SimpleNamespace(
                preferences={"google_drive_client_secrets": str(client_secrets)},
                _set_selected_photos=MagicMock(),
            )
            dialog = MagicMock()
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.imported_paths = imported_paths

            with patch("src.app.DriveImportDialog", return_value=dialog):
                AutoPostStudio._import_google_drive(owner)

            owner._set_selected_photos.assert_called_once_with(imported_paths)

    def test_client_secrets_validation_accepts_desktop_oauth_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "client.json"
            path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "client-id",
                            "client_secret": "client-secret",
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                        }
                    }
                ),
                encoding="utf-8",
            )

            validate_client_secrets(path)

    def test_client_secrets_validation_rejects_web_client_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "client.json"
            path.write_text('{"web": {}}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Desktop app"):
                validate_client_secrets(path)

    def test_listing_paginates_and_filters_to_folders_and_supported_images(self) -> None:
        pages = {
            None: {
                "files": [
                    {
                        "id": "folder-1",
                        "name": "Album",
                        "mimeType": DRIVE_FOLDER_MIME_TYPE,
                    },
                    {
                        "id": "photo-1",
                        "name": "photo.JPG",
                        "mimeType": "image/jpeg",
                    },
                    {
                        "id": "doc-1",
                        "name": "notes.pdf",
                        "mimeType": "application/pdf",
                    },
                ],
                "nextPageToken": "next",
            },
            "next": {
                "files": [
                    {
                        "id": "photo-2",
                        "name": "photo.webp",
                        "mimeType": "image/webp",
                    }
                ]
            },
        }
        service = FakeDriveService(pages)
        with patch("src.google_drive._drive_service", return_value=service):
            items = list_drive_items(Path("client.json"), "root")

        self.assertEqual(
            [(item.file_id, item.name, item.is_folder) for item in items],
            [
                ("folder-1", "Album", True),
                ("photo-1", "photo.JPG", False),
                ("photo-2", "photo.webp", False),
            ],
        )
        self.assertEqual(len(service.file_api.list_calls), 2)
        self.assertEqual(service.file_api.list_calls[0]["pageToken"], None)
        self.assertEqual(service.file_api.list_calls[1]["pageToken"], "next")
        self.assertIn("'root' in parents", str(service.file_api.list_calls[0]["q"]))
        self.assertTrue(service.file_api.list_calls[0]["supportsAllDrives"])

    def test_selected_photos_download_to_safe_unique_local_paths(self) -> None:
        service = FakeDriveService({})
        selected = [
            DriveFile("drive/123", 'summer:photo.jpg', "image/jpeg", False)
        ]
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("src.google_drive._drive_service", return_value=service),
                patch("src.google_drive.MediaIoBaseDownload", FakeDownloader),
            ):
                paths = download_drive_images(
                    Path("client.json"), selected, Path(temporary)
                )

            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].name, "summer_photo.jpg")
            self.assertEqual(paths[0].parent.name, "drive_123")
            self.assertEqual(paths[0].read_bytes(), b"downloaded drive/123")

    def test_folder_entries_are_not_downloaded_as_photos(self) -> None:
        with patch("src.google_drive._drive_service") as service_factory:
            self.assertEqual(
                download_drive_images(
                    Path("client.json"),
                    [DriveFile("folder-1", "Album", DRIVE_FOLDER_MIME_TYPE, True)],
                    Path("unused"),
                ),
                [],
            )
        service_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
