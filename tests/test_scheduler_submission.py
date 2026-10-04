"""Tests for submitting scheduler selections directly to Meta."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDateTime, QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QDateTimeEdit, QMessageBox

from src.app import SchedulerDialog
from src.scheduler import PostRecord, load_posts, save_post


class SchedulerSubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _create_dialog(
        self, folder: Path, count: int, status: str = "draft"
    ) -> SchedulerDialog:
        for index in range(count):
            image = folder / f"photo-{index}.png"
            save_post(
                PostRecord(
                    image=str(image),
                    page_id="page-1",
                    status=status,
                ),
                folder,
            )
        dialog = SchedulerDialog(folder)
        selection = dialog.table.selectionModel()
        for row in range(count):
            selection.select(
                dialog.table.model().index(row, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        return dialog

    def test_album_schedule_is_submitted_to_meta_not_locally_queued(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            dialog = self._create_dialog(folder, 2)
            dialog.post_mode.setCurrentIndex(dialog.post_mode.findData("album"))
            scheduled_at = datetime.now() + timedelta(days=1)
            date_text = scheduled_at.strftime("%Y-%m-%d %H:%M")
            dialog.album_schedule.setDateTime(
                QDateTime.fromString(date_text, "yyyy-MM-dd HH:mm")
            )
            with (
                patch.object(dialog, "_facebook_token", return_value="test-token"),
                patch.object(dialog, "_start_publish_worker") as submit,
                patch(
                    "src.app.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
            ):
                dialog._submit_schedule()

            submit.assert_called_once()
            records, token, scheduled, schedule_times = submit.call_args.args
            self.assertEqual(token, "test-token")
            self.assertTrue(scheduled)
            self.assertTrue(submit.call_args.kwargs["album"])
            self.assertEqual(len(records), 2)
            self.assertEqual(schedule_times, [scheduled_at.replace(second=0, microsecond=0)] * 2)
            saved = load_posts(folder)
            self.assertEqual({record.status for record in saved}, {"draft"})
            self.assertEqual(
                {record.scheduled_at for record in saved}, {date_text}
            )
            dialog.close()

    def test_existing_local_queue_item_can_be_submitted_to_meta(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            dialog = self._create_dialog(folder, 1, status="queued")
            schedule = datetime.now() + timedelta(days=1)
            row_schedule = dialog.table.cellWidget(0, 2)
            self.assertIsInstance(row_schedule, QDateTimeEdit)
            row_schedule.setDateTime(
                QDateTime.fromString(
                    schedule.strftime("%Y-%m-%d %H:%M"), "yyyy-MM-dd HH:mm"
                )
            )
            with (
                patch.object(dialog, "_facebook_token", return_value="test-token"),
                patch.object(dialog, "_start_publish_worker") as submit,
                patch(
                    "src.app.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
            ):
                dialog._submit_schedule()

            submit.assert_called_once()
            self.assertTrue(submit.call_args.args[2])
            self.assertFalse(submit.call_args.kwargs["album"])
            self.assertEqual(
                load_posts(folder)[0].scheduled_at,
                schedule.strftime("%Y-%m-%d %H:%M"),
            )
            dialog.close()

    def test_meta_acceptance_is_saved_as_scheduled_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            dialog = self._create_dialog(folder, 2)
            records = dialog._selected_records()
            with patch("src.app.QMessageBox.information"):
                dialog._publish_completed(
                    records,
                    [(0, {"id": "meta-post-1"})],
                    "",
                    scheduled=True,
                    album=True,
                )

            saved = load_posts(folder)
            self.assertEqual({record.status for record in saved}, {"scheduled"})
            self.assertEqual({record.remote_id for record in saved}, {"meta-post-1"})
            self.assertEqual(len({record.group_id for record in saved}), 1)
            dialog.close()

    def test_caption_destination_page_id_and_status_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dialog = self._create_dialog(Path(temporary), 1)
            for column in (1, 3, 4, 5):
                item = dialog.table.item(0, column)
                self.assertIsNotNone(item)
                self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsEditable)
            dialog.close()

    def test_scheduled_status_explains_meta_publish_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            dialog = self._create_dialog(folder, 2)
            records = dialog._selected_records()
            with patch("src.app.QMessageBox.information"):
                dialog._publish_completed(
                    records,
                    [(0, {"id": "meta-post-1"})],
                    "",
                    scheduled=True,
                    album=True,
                )

            status_item = dialog.table.item(0, 5)
            self.assertIsNotNone(status_item)
            self.assertIn("does not track", status_item.toolTip())
            self.assertIn("Meta Business Suite", status_item.toolTip())
            dialog.close()


if __name__ == "__main__":
    unittest.main()
