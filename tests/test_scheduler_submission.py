"""Tests for submitting scheduler selections directly to Meta."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QDateTime, QItemSelectionModel, QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QDateTimeEdit,
    QMessageBox,
)

from src.app import CalendarDateTimeEdit, MonthOnlyCalendar, SchedulerDialog
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

    def test_schedule_date_controls_fit_inside_visible_table_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dialog = self._create_dialog(Path(temporary), 2)

            for row in range(dialog.table.rowCount()):
                schedule = dialog.table.cellWidget(row, 2)
                self.assertIsInstance(schedule, QDateTimeEdit)
                self.assertGreaterEqual(
                    dialog.table.rowHeight(row),
                    schedule.minimumSizeHint().height(),
                )

            dialog.close()

    def test_unscheduled_calendar_opens_on_today_without_week_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dialog = self._create_dialog(Path(temporary), 1)
            schedule = dialog.table.cellWidget(0, 2)
            self.assertIsInstance(schedule, QDateTimeEdit)
            calendar = schedule.calendar
            self.assertIsInstance(calendar, QCalendarWidget)

            today = QDate.currentDate()
            self.assertEqual(schedule.minimumDate(), today)
            self.assertEqual(schedule.dateTime(), schedule.minimumDateTime())
            self.assertEqual(calendar.yearShown(), today.year())
            self.assertEqual(calendar.monthShown(), today.month())
            self.assertEqual(
                calendar.verticalHeaderFormat(),
                QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader,
            )
            self.assertEqual(
                calendar.horizontalHeaderFormat(),
                QCalendarWidget.HorizontalHeaderFormat.ShortDayNames,
            )
            self.assertTrue(calendar.isGridVisible())
            dialog.close()

    def test_calendar_button_toggles_calendar_and_selects_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dialog = self._create_dialog(Path(temporary), 1)
            schedule = dialog.table.cellWidget(0, 2)
            self.assertIsInstance(schedule, CalendarDateTimeEdit)
            self.assertFalse(schedule.calendar_button.icon().isNull())
            calendar = schedule.calendar
            self.assertIsNotNone(calendar)

            schedule.calendar_button.click()
            self.application.processEvents()
            self.assertTrue(calendar.isVisible())
            self.assertGreaterEqual(calendar.height(), 260)

            selected_date = QDate.currentDate().addDays(1)
            calendar.clicked.emit(selected_date)
            self.assertEqual(schedule.dateTime().date(), selected_date)
            self.assertFalse(calendar.isVisible())

            schedule.calendar_button.click()
            self.application.processEvents()
            self.assertTrue(calendar.isVisible())
            schedule.calendar_button.click()
            self.application.processEvents()
            self.assertFalse(calendar.isVisible())
            dialog.close()

    def test_calendar_hides_days_outside_the_displayed_month(self) -> None:
        calendar = MonthOnlyCalendar()
        calendar.setCurrentPage(2028, 12)
        outside_date = QDate(2028, 12, 1).addDays(-1)
        image = QImage(40, 30, QImage.Format.Format_ARGB32)
        image.fill("#ff00ff")
        painter = QPainter(image)

        calendar.paintCell(painter, QRect(0, 0, 40, 30), outside_date)

        painter.end()
        self.assertEqual(
            image.pixelColor(5, 5),
            calendar.palette().color(QPalette.ColorRole.Base),
        )
        self.assertEqual(calendar.monthShown(), 12)
        self.assertEqual(calendar.yearShown(), 2028)

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
