"""Tests for multi-Page Facebook credential routing."""

from __future__ import annotations

import os
import unittest
from unittest.mock import call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.app import (
    AutoPostStudio,
    PublishWorker,
    _configured_facebook_pages,
    _facebook_page_secret_name,
)
from src.scheduler import PostRecord


class FacebookPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_legacy_single_page_preferences_are_loaded(self) -> None:
        self.assertEqual(
            _configured_facebook_pages({"facebook_page_id": "12345"}),
            [{"name": "12345", "page_id": "12345"}],
        )

    def test_saved_pages_are_normalized_and_duplicate_ids_are_ignored(self) -> None:
        preferences = {
            "facebook_page_id": "legacy",
            "facebook_pages": [
                {"name": "Brand", "page_id": "12345"},
                {"name": "", "page_id": "67890"},
                {"name": "Duplicate", "page_id": "12345"},
                {"name": "Invalid"},
            ],
        }

        self.assertEqual(
            _configured_facebook_pages(preferences),
            [
                {"name": "Brand", "page_id": "12345"},
                {"name": "67890", "page_id": "67890"},
            ],
        )

    def test_page_tokens_use_page_specific_vault_names(self) -> None:
        self.assertEqual(
            _facebook_page_secret_name("12345"),
            "facebook_page_token_12345",
        )

    def test_main_window_shows_and_switches_the_current_page(self) -> None:
        preferences = {
            "facebook_pages": [
                {"name": "Brand A", "page_id": "page-a"},
                {"name": "Brand B", "page_id": "page-b"},
            ],
            "active_facebook_page_id": "page-b",
        }
        with (
            patch("src.app.configure_logging"),
            patch("src.app.load_preferences", return_value=preferences),
            patch("src.app.load_activity", return_value=[]),
            patch("src.app.save_json"),
        ):
            window = AutoPostStudio()
            self.assertEqual(
                window.current_page_combo.currentData(), "page-b"
            )
            self.assertIn("Brand B", window.current_page_combo.currentText())

            window.current_page_combo.setCurrentIndex(0)

            self.assertEqual(window.active_facebook_page_id, "page-a")
            self.assertEqual(
                window.preferences["active_facebook_page_id"], "page-a"
            )
            window.close()

    def test_publish_worker_uses_each_records_page_token(self) -> None:
        records = [
            PostRecord(image="first.jpg", caption="First", page_id="page-a"),
            PostRecord(image="second.jpg", caption="Second", page_id="page-b"),
        ]
        worker = PublishWorker(
            records,
            {"page-a": "token-a", "page-b": "token-b"},
            scheduled=False,
            schedule_times=[None, None],
            album=False,
        )

        with patch("src.app.post_photo", return_value={"id": "post"}) as post_photo:
            worker.run()

        self.assertEqual(
            post_photo.call_args_list,
            [
                call("page-a", "token-a", "first.jpg", "First", None),
                call("page-b", "token-b", "second.jpg", "Second", None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
