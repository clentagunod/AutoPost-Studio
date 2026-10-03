"""Tests for locally scheduled background publishing."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.scheduler import PostRecord, load_posts, save_post
from src.scheduler_service import _probe_provider, process_due_posts


class SchedulerServiceTests(unittest.TestCase):
    def test_openrouter_credential_probe_uses_saved_api_key(self) -> None:
        from unittest.mock import Mock

        response = Mock(status_code=200, ok=True)
        with patch("src.scheduler_service.requests.get", return_value=response) as get:
            valid = _probe_provider(
                "openrouter",
                "https://openrouter.ai/api/v1",
                "openrouter-test-key",
            )

        self.assertTrue(valid)
        get.assert_called_once_with(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": "Bearer openrouter-test-key"},
            timeout=(5, 15),
        )

    def test_due_post_is_published_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            record = PostRecord(
                image=str(folder / "photo.png"),
                scheduled_at="2026-10-03 10:00",
                page_id="page-1",
                caption="Hello",
                status="queued",
            )
            save_post(record, folder)
            with (
                patch("src.scheduler_service.get_secret", return_value="page-token"),
                patch(
                    "src.scheduler_service.post_photo",
                    return_value={"id": "post-1"},
                ) as publish,
                patch("src.scheduler_service.notify") as notify,
            ):
                processed = process_due_posts(
                    folder, now=datetime(2026, 10, 3, 10, 1)
                )

            self.assertEqual(processed, 1)
            publish.assert_called_once_with(
                "page-1", "page-token", record.image, "Hello"
            )
            saved = load_posts(folder)[0]
            self.assertEqual(saved.status, "published")
            self.assertEqual(saved.remote_id, "post-1")
            notify.assert_called_once()

    def test_future_post_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            record = PostRecord(
                image=str(folder / "later.png"),
                scheduled_at="2026-10-03 11:00",
                page_id="page-1",
                status="queued",
            )
            save_post(record, folder)
            with patch("src.scheduler_service.post_photo") as publish:
                processed = process_due_posts(
                    folder, now=datetime(2026, 10, 3, 10, 59)
                )
            self.assertEqual(processed, 0)
            publish.assert_not_called()
            self.assertEqual(load_posts(folder)[0].status, "queued")

    def test_queued_album_publishes_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            records = [
                PostRecord(
                    image=str(folder / f"photo-{index}.png"),
                    scheduled_at="2026-10-03 10:00",
                    page_id="page-1",
                    caption="Album caption" if index == 0 else "ignored",
                    status="queued",
                    group_id="album-1",
                )
                for index in range(2)
            ]
            for record in records:
                save_post(record, folder)
            with (
                patch("src.scheduler_service.get_secret", return_value="page-token"),
                patch(
                    "src.scheduler_service.post_album",
                    return_value={"id": "album-post"},
                ) as publish,
                patch("src.scheduler_service.notify"),
            ):
                process_due_posts(folder, now=datetime(2026, 10, 3, 10, 1))

            publish.assert_called_once_with(
                "page-1",
                "page-token",
                [record.image for record in records],
                "Album caption",
            )
            saved = load_posts(folder)
            self.assertEqual({record.status for record in saved}, {"published"})
            self.assertEqual({record.remote_id for record in saved}, {"album-post"})

    def test_auth_failure_marks_post_failed_and_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            record = PostRecord(
                image=str(folder / "photo.png"),
                scheduled_at="2026-10-03 10:00",
                page_id="page-1",
                status="queued",
            )
            save_post(record, folder)
            with (
                patch("src.scheduler_service.get_secret", return_value="bad-token"),
                patch(
                    "src.scheduler_service.post_photo",
                    side_effect=RuntimeError("Facebook session is invalid"),
                ),
                patch("src.scheduler_service.notify") as notify,
            ):
                process_due_posts(folder, now=datetime(2026, 10, 3, 10, 1))
            failed = load_posts(folder)[0]
            self.assertEqual(failed.status, "failed")
            self.assertIn("session is invalid", failed.last_error)
            self.assertEqual(
                notify.call_args.args[0], "Facebook token needs attention"
            )


if __name__ == "__main__":
    unittest.main()
