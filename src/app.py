"""AutoPost Studio desktop application."""

from __future__ import annotations

import logging
import os
import platform
import shlex
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from PySide6.QtCore import QDate, QDateTime, QTime, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox as QtMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .captions import generate_caption
from .captions import PROVIDER_DEFAULTS
from .config import APP_NAME, APP_VERSION, DEFAULT_FRAME_PATH, ICON_PATH
from .facebook import FacebookError, post_album, post_photo, validate_page_access
from .imaging import (
    SUPPORTED_EXTS,
    attach_frame,
    load_frame,
    load_photo,
    process_one,
    save_image,
)
from .scheduler import PostRecord, delete_post_files, load_posts, save_post
from .storage import (
    APP_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
    configure_logging,
    load_activity,
    load_preferences,
    record_activity,
    save_json,
)
from .vault import get_secret, set_secret

COLORS = {
    "background": "#f4f6f8",
    "surface": "#ffffff",
    "ink": "#17212b",
    "muted": "#73808c",
    "line": "#e2e7ec",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "stage": "#e9eef4",
    "success": "#15803d",
    "danger": "#b42318",
    "field": "#fbfcfd",
}
DARK_COLORS = {
    "background": "#111820",
    "surface": "#19232e",
    "ink": "#e7edf4",
    "muted": "#98a7b6",
    "line": "#2b3947",
    "accent": "#5b8def",
    "accent_hover": "#79a2f4",
    "stage": "#121b24",
    "success": "#4ade80",
    "danger": "#ff8178",
    "field": "#141e28",
}
IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)"


class QMessageBox(QtMessageBox):
    """Standard application notices with an option to copy their text."""

    @staticmethod
    def information(
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton = QtMessageBox.StandardButton.Ok,
        defaultButton: QtMessageBox.StandardButton = QtMessageBox.StandardButton.NoButton,
    ) -> QtMessageBox.StandardButton:
        return QMessageBox._show_copyable(
            QtMessageBox.Icon.Information, parent, title, text, buttons, defaultButton
        )

    @staticmethod
    def warning(
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton = QtMessageBox.StandardButton.Ok,
        defaultButton: QtMessageBox.StandardButton = QtMessageBox.StandardButton.NoButton,
    ) -> QtMessageBox.StandardButton:
        return QMessageBox._show_copyable(
            QtMessageBox.Icon.Warning, parent, title, text, buttons, defaultButton
        )

    @staticmethod
    def critical(
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton = QtMessageBox.StandardButton.Ok,
        defaultButton: QtMessageBox.StandardButton = QtMessageBox.StandardButton.NoButton,
    ) -> QtMessageBox.StandardButton:
        return QMessageBox._show_copyable(
            QtMessageBox.Icon.Critical, parent, title, text, buttons, defaultButton
        )

    @staticmethod
    def _show_copyable(
        icon: QtMessageBox.Icon,
        parent: QWidget | None,
        title: str,
        text: str,
        buttons: QtMessageBox.StandardButton,
        default_button: QtMessageBox.StandardButton,
    ) -> QtMessageBox.StandardButton:
        dialog = QtMessageBox(icon, title, text, buttons, parent)
        dialog.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        copy_button = dialog.addButton(
            "Copy message", QtMessageBox.ButtonRole.ActionRole
        )
        dialog.setDefaultButton(default_button)
        dialog.exec()
        if dialog.clickedButton() is copy_button:
            QApplication.clipboard().setText(f"{title}\n{text}")
        return dialog.standardButton(dialog.clickedButton())


class PreviewWorker(QThread):
    ready = Signal(int, object, object)
    failed = Signal(int, str)

    def __init__(
        self, generation: int, frame_path: Path, photo_path: Path, fit: str
    ) -> None:
        super().__init__()
        self.generation = generation
        self.frame_path = frame_path
        self.photo_path = photo_path
        self.fit = fit

    def run(self) -> None:
        try:
            frame = load_frame(self.frame_path)
            photo = load_photo(self.photo_path)
            composite = attach_frame(frame, photo, self.fit)
            self.ready.emit(self.generation, composite, composite.size)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            self.failed.emit(self.generation, str(exc))


class GalleryWorker(QThread):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        generation: int,
        frame_path: Path,
        photos: list[Path],
        fit: str,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.frame_path = frame_path
        self.photos = photos[:8]
        self.fit = fit

    def run(self) -> None:
        try:
            frame = load_frame(self.frame_path)
            previews: list[tuple[str, QImage | None]] = []
            for photo_path in self.photos:
                try:
                    composite = attach_frame(frame, load_photo(photo_path), self.fit)
                    rgba = composite.convert("RGBA")
                    qimage = QImage(
                        rgba.tobytes("raw", "RGBA"),
                        rgba.width,
                        rgba.height,
                        rgba.width * 4,
                        QImage.Format.Format_RGBA8888,
                    ).copy()
                    previews.append((photo_path.name, qimage))
                except (UnidentifiedImageError, OSError, ValueError):
                    previews.append((photo_path.name, None))
            self.ready.emit(self.generation, previews)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            self.failed.emit(self.generation, str(exc))


class BatchWorker(QThread):
    progress = Signal(int, int, str)
    complete = Signal(int, int, object)
    failed = Signal(str)

    def __init__(
        self,
        frame_path: Path,
        photos: list[Path],
        output_dir: Path,
        fit: str,
        quality: int,
        image_format: str,
        caption: str,
        page_id: str,
        metadata_dir: Path,
    ) -> None:
        super().__init__()
        self.frame_path = frame_path
        self.photos = photos
        self.output_dir = output_dir
        self.fit = fit
        self.quality = quality
        self.image_format = image_format
        self.caption = caption
        self.page_id = page_id
        self.metadata_dir = metadata_dir

    def run(self) -> None:
        try:
            frame = load_frame(self.frame_path)
            succeeded = 0
            outputs: list[tuple[Path, Path]] = []
            for index, photo in enumerate(self.photos, 1):
                output = self.output_dir / (
                    f"{photo.stem}_{index:03d}_framed.{self.image_format}"
                )
                ok = process_one(
                    frame, photo, output, self.fit, (0.5, 0.5), self.quality
                )
                if ok:
                    save_post(
                        PostRecord(
                            image=str(output.resolve()),
                            caption=self.caption,
                            page_id=self.page_id,
                        ),
                        self.metadata_dir,
                    )
                    succeeded += 1
                    outputs.append((photo, output))
                self.progress.emit(index, len(self.photos), photo.name)
            self.complete.emit(succeeded, len(self.photos), outputs)
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            self.failed.emit(str(exc))


class PublishWorker(QThread):
    progress = Signal(int, int, str)
    completed = Signal(object, str)

    def __init__(
        self,
        records: list[PostRecord],
        token: str,
        scheduled: bool,
        schedule_times: list[datetime | None],
        album: bool,
    ) -> None:
        super().__init__()
        self.records = records
        self.token = token
        self.scheduled = scheduled
        self.schedule_times = schedule_times
        self.album = album

    def run(self) -> None:
        results: list[tuple[int, dict[str, Any]]] = []
        failure = ""
        try:
            if self.album:
                self.progress.emit(
                    0, 0, "Uploading album to Facebook…"
                )
                result = post_album(
                    self.records[0].page_id,
                    self.token,
                    [record.image for record in self.records],
                    self.records[0].caption,
                    self.schedule_times[0],
                )
                results.append((0, result))
            else:
                total = len(self.records)
                for index, (record, schedule_at) in enumerate(
                    zip(self.records, self.schedule_times), start=1
                ):
                    self.progress.emit(
                        -1,
                        total,
                        f"Publishing {index} of {total} · {Path(record.image).name}",
                    )
                    result = post_photo(
                        record.page_id,
                        self.token,
                        record.image,
                        record.caption,
                        schedule_at,
                    )
                    results.append((index - 1, result))
                    self.progress.emit(
                        index,
                        total,
                        f"Facebook accepted {index} of {total} post(s)",
                    )
            self.progress.emit(
                len(self.records) if not self.album else 1,
                len(self.records) if not self.album else 1,
                "Finishing up…",
            )
        except (FacebookError, OSError, ValueError) as exc:
            failure = str(exc)
        self.completed.emit(results, failure)


class PreviewGallery(QWidget):
    """Responsive eight-image grid with a concise overflow count."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        layout.addLayout(self.grid, 1)
        self.remaining = QLabel("")
        self.remaining.setObjectName("galleryRemaining")
        self.remaining.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.remaining)
        self._previews: list[tuple[str, QImage | None]] = []
        self._total = 0
        self.tiles: list[tuple[QLabel, QLabel, QFrame]] = []
        for index in range(8):
            card = QFrame()
            card.setObjectName("galleryTile")
            tile_layout = QVBoxLayout(card)
            tile_layout.setContentsMargins(8, 8, 8, 8)
            tile_layout.setSpacing(6)
            image = QLabel("Preparing preview…")
            image.setObjectName("galleryImage")
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setMinimumSize(80, 70)
            image.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            name = QLabel("")
            name.setObjectName("galleryName")
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name.setWordWrap(False)
            tile_layout.addWidget(image, 1)
            tile_layout.addWidget(name)
            self.grid.addWidget(card, index // 4, index % 4)
            self.tiles.append((image, name, card))

    def show_loading(self, count: int) -> None:
        self._previews = []
        self._total = count
        for index, (image, name, card) in enumerate(self.tiles):
            card.setVisible(index < min(count, len(self.tiles)))
            image.setPixmap(QPixmap())
            image.setText("Preparing preview…")
            name.clear()
        self.remaining.setText("")

    def set_previews(
        self,
        previews: list[tuple[str, QImage | None]],
        total: int,
    ) -> None:
        self._previews = previews
        self._total = total
        self._render_previews()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._render_previews()

    def _render_previews(self) -> None:
        previews = self._previews
        for index, (image, name, card) in enumerate(self.tiles):
            if index >= len(previews):
                card.setVisible(False)
                continue
            filename, qimage = previews[index]
            card.setVisible(True)
            name.setText(filename if len(filename) <= 24 else filename[:21] + "...")
            name.setToolTip(filename)
            if qimage is None:
                image.setPixmap(QPixmap())
                image.setText("Preview unavailable")
            else:
                image.setText("")
                image.setPixmap(
                    QPixmap.fromImage(qimage).scaled(
                        image.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        remaining = max(0, self._total - len(previews))
        self.remaining.setText(
            f"+{remaining} more images" if remaining else f"{self._total} images loaded"
        )


class PreviewCanvas(QLabel):
    def __init__(self) -> None:
        super().__init__("Load a frame and an image to preview your post")
        self.setObjectName("previewCanvas")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source: QPixmap | None = None

    def set_image(self, image: Image.Image) -> None:
        rgba = image.convert("RGBA")
        qimage = QImage(
            rgba.tobytes("raw", "RGBA"),
            rgba.width,
            rgba.height,
            rgba.width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        self._source = QPixmap.fromImage(qimage)
        self._rescale()

    def clear_image(self, message: str) -> None:
        self._source = None
        self.setPixmap(QPixmap())
        self.setText(message)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source:
            self.setPixmap(
                self._source.scaled(
                    self.contentsRect().size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )


class SchedulerDialog(QDialog):
    def __init__(
        self,
        folder: Path,
        parent: QWidget | None = None,
        default_page_id: str = "",
    ) -> None:
        super().__init__(parent)
        self.folder = folder
        self.records = load_posts(folder)
        for record in self.records:
            if not record.page_id:
                record.page_id = default_page_id
        self.setWindowTitle("Post scheduler")
        screen = self.screen()
        available = screen.availableGeometry() if screen else self.geometry()
        self.resize(
            min(1120, max(580, round(available.width() * 0.92))),
            min(560, max(360, round(available.height() * 0.82))),
        )
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Select a date from the calendar and set local time. Scheduled posts run "
            "when the AutoPost Studio background worker is active."
        )
        hint.setObjectName("mutedText")
        layout.addWidget(hint)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Image", "Caption", "Schedule (local)", "Destination", "Page ID", "Status", "ID"]
        )
        self.table.setColumnHidden(6, True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.table.setSortingEnabled(False)
        self.table.setColumnWidth(0, max(100, int(self.width() * 0.15)))
        self.table.setColumnWidth(1, max(130, int(self.width() * 0.24)))
        self.table.setColumnWidth(2, max(120, int(self.width() * 0.14)))
        self.table.setColumnWidth(3, max(110, int(self.width() * 0.12)))
        self.table.setColumnWidth(4, max(120, int(self.width() * 0.14)))
        layout.addWidget(self.table, 1)
        self.publish_status = QLabel("")
        self.publish_status.setWordWrap(True)
        self.publish_status.hide()
        layout.addWidget(self.publish_status)
        self.publish_progress = QProgressBar()
        self.publish_progress.setTextVisible(False)
        self.publish_progress.setMaximumHeight(7)
        self.publish_progress.hide()
        layout.addWidget(self.publish_progress)
        self._populate()
        publish_options = QHBoxLayout()
        publish_options.addWidget(QLabel("Selected images publish as"))
        self.post_mode = QComboBox()
        self.post_mode.addItem("Individual posts", "individual")
        self.post_mode.addItem("One album post", "album")
        self.post_mode.setToolTip(
            "Individual posts create one Page post per selected image. "
            "One album post combines selected images into one multi-photo post."
        )
        publish_options.addWidget(self.post_mode)
        publish_options.addStretch(1)
        layout.addLayout(publish_options)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        save_button = buttons.addButton(
            "Save changes", QDialogButtonBox.ButtonRole.ApplyRole
        )
        post_button = buttons.addButton(
            "Publish selection now", QDialogButtonBox.ButtonRole.ActionRole
        )
        save_button.clicked.connect(lambda: self._save_changes())
        schedule_button = buttons.addButton(
            "Queue selection", QDialogButtonBox.ButtonRole.ActionRole
        )
        schedule_button.clicked.connect(self._submit_schedule)
        post_button.clicked.connect(self._post_selected)
        self.publish_action_buttons = [save_button, post_button, schedule_button]
        delete_button = buttons.addButton(
            "Delete selected", QDialogButtonBox.ButtonRole.DestructiveRole
        )
        delete_button.clicked.connect(self._delete_selected)
        self.delete_button = delete_button
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self._publish_worker: PublishWorker | None = None
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5_000)
        self._status_timer.timeout.connect(self._refresh_background_statuses)
        self._status_timer.start()
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self) -> None:
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            values = (
                Path(record.image).name,
                record.caption,
                "",
                record.destination,
                record.page_id,
                record.status,
                record.id,
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col in {0, 5, 6}:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(record.image if col == 0 else value)
                self.table.setItem(row, col, item)
            schedule = QDateTimeEdit()
            schedule.setCalendarPopup(True)
            schedule.setDisplayFormat("yyyy-MM-dd HH:mm")
            schedule.setMinimumDateTime(
                QDateTime(QDate(2000, 1, 1), QTime(0, 0))
            )
            schedule.setSpecialValueText("Not scheduled")
            if record.scheduled_at:
                try:
                    scheduled_at = datetime.strptime(
                        record.scheduled_at, "%Y-%m-%d %H:%M"
                    )
                    schedule.setDateTime(
                        QDateTime(
                            QDate(
                                scheduled_at.year,
                                scheduled_at.month,
                                scheduled_at.day,
                            ),
                            QTime(scheduled_at.hour, scheduled_at.minute),
                        )
                    )
                except ValueError:
                    schedule.setToolTip(
                        f"Invalid saved date/time: {record.scheduled_at}. Choose a valid date."
                    )
            else:
                schedule.setDateTime(schedule.minimumDateTime())
            self.table.setCellWidget(row, 2, schedule)
            if record.last_error:
                self.table.item(row, 5).setToolTip(record.last_error)

    def _refresh_background_statuses(self) -> None:
        try:
            latest = {record.id: record for record in load_posts(self.folder)}
        except (OSError, ValueError):
            logging.getLogger("frame_studio").exception(
                "Could not refresh scheduler status"
            )
            return
        for row, record in enumerate(self.records):
            updated = latest.get(record.id)
            if updated is None:
                continue
            record.status = updated.status
            record.remote_id = updated.remote_id
            record.last_error = updated.last_error
            status_item = self.table.item(row, 5)
            status_item.setText(updated.status)
            status_item.setToolTip(updated.last_error or updated.remote_id)

    def _save_changes(self, show_message: bool = True) -> bool:
        try:
            updates: list[tuple[PostRecord, str, str, str, str]] = []
            for row, record in enumerate(self.records):
                caption = self.table.item(row, 1).text()
                schedule = self.table.cellWidget(row, 2)
                if not isinstance(schedule, QDateTimeEdit):
                    raise ValueError("A schedule date/time control is missing.")
                scheduled_text = (
                    ""
                    if schedule.dateTime() == schedule.minimumDateTime()
                    else schedule.dateTime().toString("yyyy-MM-dd HH:mm")
                )
                if scheduled_text:
                    scheduled_at = datetime.strptime(
                        scheduled_text, "%Y-%m-%d %H:%M"
                    )
                    if (
                        record.status in {"draft", "queued"}
                        and scheduled_at <= datetime.now()
                    ):
                        raise ValueError(
                            f"Choose a future date and time for {Path(record.image).name}."
                        )
                destination = self.table.item(row, 3).text().strip()
                page_id = self.table.item(row, 4).text().strip()
                updates.append(
                    (record, caption, scheduled_text, destination, page_id)
                )
            for record, caption, scheduled_text, destination, page_id in updates:
                record.caption = caption
                record.scheduled_at = scheduled_text
                record.destination = destination
                record.page_id = page_id
                save_post(record, self.folder)
            if show_message:
                QMessageBox.information(self, "Scheduler", "Changes saved.")
            return True
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save schedule", str(exc))
            return False

    def _post_selected(self) -> None:
        records = self._selected_records()
        if not records:
            QMessageBox.warning(
                self,
                "Select images",
                "Select one or more image rows. Use Ctrl-click or Shift-click to select multiple images.",
            )
            return
        if not self._save_changes(show_message=False):
            return
        if self.post_mode.currentData() == "album":
            self._publish_album(records)
            return
        self._publish_individual(records, scheduled=False)

    def _submit_schedule(self) -> None:
        records = self._selected_records()
        if not records:
            QMessageBox.warning(
                self,
                "Select images",
                "Select one or more image rows before submitting a schedule.",
            )
            return
        if not self._save_changes(show_message=False):
            return
        album = self.post_mode.currentData() == "album"
        if album:
            if not 2 <= len(records) <= 10:
                QMessageBox.warning(
                    self, "Select 2–10 images", "A scheduled album requires 2 to 10 images."
                )
                return
            if len({record.page_id for record in records}) != 1 or not records[0].page_id:
                QMessageBox.warning(
                    self, "One Page required", "All album images must have the same Page ID."
                )
                return
            times = {record.scheduled_at for record in records}
            if "" in times or len(times) != 1:
                QMessageBox.warning(
                    self,
                    "Use one schedule time",
                    "For an album, select the same future date and time on every selected row.",
                )
                return
        if not album:
            missing_time = next(
                (record for record in records if not record.scheduled_at), None
            )
            if missing_time is not None:
                QMessageBox.warning(
                    self, "Schedule time required",
                    f"Choose a date and time for {Path(missing_time.image).name}.",
                )
                return
        group_id = uuid.uuid4().hex if album else ""
        try:
            for record in records:
                record.group_id = group_id
                record.status = "queued"
                record.last_error = ""
                save_post(record, self.folder)
        except OSError as exc:
            QMessageBox.critical(self, "Could not queue posts", str(exc))
            self.records = load_posts(self.folder)
            self._populate()
            return
        self._populate()
        self.publish_status.setText(
            f"Queued {len(records)} post(s). The background worker will publish them at the selected local time."
        )
        self.publish_status.show()
        QMessageBox.information(
            self, "Posts queued", f"{len(records)} post(s) queued for local publishing."
        )

    def _delete_selected(self) -> None:
        records = self._selected_records()
        if not records:
            QMessageBox.warning(self, "Select posts", "Select one or more scheduler rows to delete.")
            return
        answer = QMessageBox.question(
            self,
            "Delete selected posts?",
            f"Delete {len(records)} selected scheduler item(s), their sidecar files, "
            "and associated images? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_post_files(records, self.folder)
        except OSError as exc:
            self.records = load_posts(self.folder)
            self._populate()
            QMessageBox.critical(self, "Some files could not be deleted", str(exc))
            return
        removed_ids = {record.id for record in records}
        self.records = [
            record for record in self.records if record.id not in removed_ids
        ]
        self._populate()
        self.publish_status.setText(f"Deleted {len(records)} scheduler item(s) and associated files.")
        self.publish_status.show()

    def _selected_records(self) -> list[PostRecord]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        return [self.records[row] for row in rows]

    def _facebook_token(self) -> str | None:
        try:
            token = get_secret("facebook_page_token")
        except RuntimeError as exc:
            QMessageBox.critical(self, "Credential vault unavailable", str(exc))
            return None
        if not token:
            QMessageBox.warning(
                self, "Facebook setup required", "Add a Page access token in Options → APIs."
            )
            return None
        return token

    def _publish_individual(
        self, records: list[PostRecord], scheduled: bool
    ) -> None:
        schedule_times: list[datetime | None] = []
        if scheduled:
            try:
                for record in records:
                    if not record.scheduled_at:
                        raise ValueError(
                            f"Enter a schedule time for {Path(record.image).name}."
                        )
                    schedule_times.append(
                        datetime.strptime(record.scheduled_at, "%Y-%m-%d %H:%M")
                    )
            except ValueError as exc:
                QMessageBox.warning(self, "Schedule time required", str(exc))
                return
        else:
            schedule_times = [None] * len(records)
        if any(not record.page_id for record in records):
            QMessageBox.warning(
                self, "Page ID required", "Enter a Facebook Page ID for every selected image."
            )
            return
        token = self._facebook_token()
        if not token:
            return
        action = "schedule" if scheduled else "publish"
        answer = QMessageBox.question(
            self,
            f"Confirm {action}",
            f"{'Submit' if scheduled else 'Publish'} {len(records)} selected image(s) "
            f"as individual Facebook Page posts?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._start_publish_worker(
            records, token, scheduled, schedule_times, album=False
        )

    def _publish_album(
        self, records: list[PostRecord], scheduled: bool = False
    ) -> None:
        if not 2 <= len(records) <= 10:
            QMessageBox.warning(
                self, "Select 2–10 images", "A Facebook album post requires 2 to 10 selected images."
            )
            return
        page_ids = {record.page_id for record in records}
        if "" in page_ids or len(page_ids) != 1:
            QMessageBox.warning(
                self,
                "One Page required",
                "All images in one album must have the same Facebook Page ID.",
            )
            return
        schedule_at: datetime | None = None
        if scheduled:
            schedule_values = {record.scheduled_at for record in records}
            if "" in schedule_values or len(schedule_values) != 1:
                QMessageBox.warning(
                    self,
                    "Use one schedule time",
                    "For an album, enter the same schedule time on every selected row.",
                )
                return
            try:
                schedule_at = datetime.strptime(
                    records[0].scheduled_at, "%Y-%m-%d %H:%M"
                )
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid schedule time", "Use YYYY-MM-DD HH:MM in local time."
                )
                return
        token = self._facebook_token()
        if not token:
            return
        names = "\n".join(f"• {Path(record.image).name}" for record in records)
        caption = records[0].caption
        action = "Schedule" if scheduled else "Publish"
        prompt = (
            f"{action} these {len(records)} images as one Facebook album post?\n\n{names}\n\n"
            f"Page ID: {records[0].page_id}\n"
            f"Caption: {caption or '(no caption)'}\n"
            f"Schedule: {records[0].scheduled_at if scheduled else 'Now'}\n\n"
            "The first selected image's caption and schedule time will be used."
        )
        answer = QMessageBox.question(
            self,
            f"Confirm album {action.lower()}",
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_publish_worker(
            records,
            token,
            scheduled,
            [schedule_at] * len(records),
            album=True,
        )

    def _start_publish_worker(
        self,
        records: list[PostRecord],
        token: str,
        scheduled: bool,
        schedule_times: list[datetime | None],
        album: bool,
    ) -> None:
        self._set_publish_busy(True)
        self.publish_status.setStyleSheet("")
        self.publish_progress.setStyleSheet("")
        self.publish_status.setText(
            "Uploading album to Facebook…"
            if album
            else f"Starting Facebook {'schedule' if scheduled else 'publishing'}…"
        )
        self.publish_status.show()
        self.publish_progress.setRange(0, 0)
        self.publish_progress.show()
        worker = PublishWorker(records, token, scheduled, schedule_times, album)
        worker.progress.connect(self._publish_progress)
        worker.finished.connect(self._publish_thread_finished)
        worker.completed.connect(
            lambda results, failure: self._publish_completed(
                records, results, failure, scheduled, album
            )
        )
        self._publish_worker = worker
        worker.start()

    def _set_publish_busy(self, busy: bool) -> None:
        for button in self.publish_action_buttons:
            button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        if busy:
            self.setWindowTitle("Post scheduler · Publishing…")
        else:
            self.setWindowTitle("Post scheduler")

    def _publish_progress(self, current: int, total: int, message: str) -> None:
        self.publish_status.setText(message)
        if current < 0 or not total:
            self.publish_progress.setRange(0, 0)
        else:
            self.publish_progress.setRange(0, total)
            self.publish_progress.setValue(current)

    def _publish_completed(
        self,
        records: list[PostRecord],
        results: list[tuple[int, dict[str, Any]]],
        failure: str,
        scheduled: bool,
        album: bool,
    ) -> None:
        completed = 0
        local_errors: list[str] = []
        status = "scheduled" if scheduled else "published"
        if album and results:
            post_id = str(results[0][1].get("id") or "")
            for record in records:
                record.status = status
                record.remote_id = post_id
                try:
                    save_post(record, self.folder)
                except OSError as exc:
                    local_errors.append(f"{Path(record.image).name}: {exc}")
            completed = len(records)
        elif not album:
            for index, result in results:
                record = records[index]
                record.status = status
                record.remote_id = str(
                    result.get("post_id") or result.get("id") or ""
                )
                completed += 1
                try:
                    save_post(record, self.folder)
                except OSError as exc:
                    local_errors.append(f"{Path(record.image).name}: {exc}")
        self._populate()
        total = len(records)
        if not failure and not local_errors:
            message = (
                f"Completed: {completed} of {total} post(s) "
                f"{'scheduled' if scheduled else 'published'} successfully."
            )
            if album and results:
                message += f" Facebook post ID: {results[0][1].get('id') or 'OK'}."
            self.publish_status.setStyleSheet(
                f"color: {self.parent().colors['success']};"
                if isinstance(self.parent(), AutoPostStudio)
                else "color: #15803d;"
            )
            self.publish_progress.setStyleSheet(
                "QProgressBar::chunk { background: #15803d; }"
            )
            self.publish_progress.setRange(0, 1)
            self.publish_progress.setValue(1)
            QMessageBox.information(self, "Publishing complete", message)
        else:
            details = [part for part in [failure, *local_errors] if part]
            message = f"Failed or incomplete: {completed} of {total} completed. " + " ".join(details)
            self.publish_status.setStyleSheet("color: #b42318;")
            self.publish_progress.setStyleSheet(
                "QProgressBar::chunk { background: #b42318; }"
            )
            self.publish_progress.setRange(0, max(1, total))
            self.publish_progress.setValue(min(completed, total))
            QMessageBox.warning(self, "Publishing did not fully complete", message)
        self.publish_status.setText(message)

    def _publish_thread_finished(self) -> None:
        self._publish_worker = None
        self._set_publish_busy(False)

    def closeEvent(self, event: Any) -> None:
        if self._publish_worker is not None and self._publish_worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._publish_worker is not None and self._publish_worker.isRunning():
            return
        super().reject()


class TerminalConsole(QPlainTextEdit):
    """Single-surface command prompt that protects previously printed output."""

    def __init__(self, submit_command: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.submit_command = submit_command
        self.command_history: list[str] = []
        self.history_index = 0
        self.input_start = 0
        self.setObjectName("terminalOutput")
        self.setUndoRedoEnabled(False)
        self.appendPlainText(
            f"{APP_NAME} {APP_VERSION} · app automation console\n"
            "Type `help` for commands. This is an app CLI, not a general OS shell."
        )
        self._write_prompt()

    def _end_cursor(self) -> Any:
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        return cursor

    def _write_prompt(self) -> None:
        cursor = self._end_cursor()
        if cursor.position() > 0:
            cursor.insertText("\n")
        cursor.insertText("autopost> ")
        self.input_start = cursor.position()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def _current_command(self) -> str:
        cursor = self._end_cursor()
        end = cursor.position()
        cursor.setPosition(self.input_start)
        cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
        return cursor.selectedText()

    def _show_result(self, result: str) -> None:
        cursor = self._end_cursor()
        if result:
            cursor.insertText(result.rstrip("\n") + "\n")
        cursor.insertText("autopost> ")
        self.input_start = cursor.position()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def _replace_current_command(self, value: str) -> None:
        cursor = self._end_cursor()
        cursor.setPosition(self.input_start)
        cursor.movePosition(cursor.MoveOperation.End, cursor.MoveMode.KeepAnchor)
        cursor.insertText(value)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: Any) -> None:
        key = event.key()
        modifiers = event.modifiers()
        cursor = self.textCursor()
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            command = self._current_command().strip()
            if command:
                self.command_history.append(command)
            self.history_index = len(self.command_history)
            cursor = self._end_cursor()
            cursor.insertText("\n")
            self.setTextCursor(cursor)
            if command.casefold() == "clear":
                self.clear()
                self._write_prompt()
                return
            try:
                result = self.submit_command(command) if command else ""
            except Exception as exc:
                logging.getLogger("frame_studio").exception(
                    "In-app terminal command failed"
                )
                result = f"error: {exc}"
            self._show_result(result)
            return
        if key == Qt.Key.Key_Up:
            if self.command_history:
                self.history_index = max(0, self.history_index - 1)
                self._replace_current_command(
                    self.command_history[self.history_index]
                )
            return
        if key == Qt.Key.Key_Down:
            if self.command_history:
                self.history_index = min(
                    len(self.command_history), self.history_index + 1
                )
                value = (
                    self.command_history[self.history_index]
                    if self.history_index < len(self.command_history)
                    else ""
                )
                self._replace_current_command(value)
            return
        if key == Qt.Key.Key_Home:
            cursor.setPosition(self.input_start)
            self.setTextCursor(cursor)
            return
        if key == Qt.Key.Key_End:
            cursor.movePosition(cursor.MoveOperation.End)
            self.setTextCursor(cursor)
            return
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_A:
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.setPosition(self.input_start, cursor.MoveMode.KeepAnchor)
                self.setTextCursor(cursor)
                return
            if key == Qt.Key.Key_C and not cursor.hasSelection():
                self._replace_current_command("")
                return
        if cursor.hasSelection():
            selection_start = min(cursor.anchor(), cursor.position())
            if selection_start < self.input_start:
                cursor = self._end_cursor()
                self.setTextCursor(cursor)
        elif cursor.position() < self.input_start:
            cursor = self._end_cursor()
            self.setTextCursor(cursor)
        if key == Qt.Key.Key_Backspace and cursor.position() <= self.input_start:
            return
        if key == Qt.Key.Key_Delete and cursor.position() < self.input_start:
            return
        super().keyPressEvent(event)

    def cut(self) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection() and min(cursor.anchor(), cursor.position()) >= self.input_start:
            super().cut()
            return
        self.setTextCursor(self._end_cursor())

    def insertFromMimeData(self, source: Any) -> None:
        cursor = self.textCursor()
        if (
            cursor.position() < self.input_start
            or (
                cursor.hasSelection()
                and min(cursor.anchor(), cursor.position()) < self.input_start
            )
        ):
            self.setTextCursor(self._end_cursor())
        super().insertFromMimeData(source)


class TerminalDialog(QDialog):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("AutoPost Studio · Console")
        screen = self.screen()
        available = screen.availableGeometry() if screen else self.geometry()
        self.resize(
            min(820, max(520, round(available.width() * 0.8))),
            min(470, max(320, round(available.height() * 0.7))),
        )
        self.setObjectName("terminalDialog")
        layout = QVBoxLayout(self)
        self.terminal = TerminalConsole(self._execute_command, self)
        layout.addWidget(self.terminal, 1)
        self.terminal.setFocus()

    def _execute_command(self, text: str) -> str:
        try:
            args = shlex.split(text, posix=os.name != "nt")
        except ValueError as exc:
            return f"error: {exc}"
        if os.name == "nt":
            args = [
                value[1:-1]
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
                else value
                for value in args
            ]
        if args == ["help"]:
            args = ["--help"]
        if not args:
            return ""
        command = args[0].lower()
        if command in {"open-outputs", "outputs"} and len(args) == 1:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(OUTPUT_DIR)))
            return f"Opened {OUTPUT_DIR}"
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from .automation_cli import main as automation_cli_main

        stdout = StringIO()
        stderr = StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = automation_cli_main(args)
        except SystemExit as exc:
            exit_code = int(exc.code) if isinstance(exc.code, int) else 0
        result = stdout.getvalue().strip()
        errors = stderr.getvalue().strip()
        if errors:
            result = f"{result}\n{errors}".strip()
        if exit_code and not result:
            result = f"Command exited with status {exit_code}."
        if text.casefold() == "help":
            result += (
                "\nIn-app commands: open-outputs, outputs, clear"
            )
        return result


class AutoPostStudio(QMainWindow):
    """Main composition workspace and entry point to post-management tools."""

    def __init__(self) -> None:
        super().__init__()
        configure_logging()
        self.log = logging.getLogger("frame_studio")
        self.preferences = load_preferences()
        self.dark_mode = bool(self.preferences.get("dark_mode", False))
        self.colors = dict(DARK_COLORS if self.dark_mode else COLORS)
        default_frame = DEFAULT_FRAME_PATH
        default_photo = APP_DIR / "image.jpg"
        self.frame_path = QLineEdit(
            str(
                self.preferences.get(
                    "frame_path", str(default_frame if default_frame.is_file() else "")
                )
            )
        )
        self.photo_path = QLineEdit(
            str(
                self.preferences.get(
                    "photo_path", str(default_photo if default_photo.is_file() else "")
                )
            )
        )
        self.output_dir = QLineEdit(
            str(self.preferences.get("output_dir") or OUTPUT_DIR)
        )
        self.fit = str(self.preferences.get("fit", "cover"))
        if self.fit not in {"cover", "contain"}:
            self.fit = "cover"
        self.caption = QPlainTextEdit(str(self.preferences.get("caption", "")))
        self._preview_generation = 0
        self._workers: set[QThread] = set()
        self._batch: BatchWorker | None = None
        self._batch_frame: Path | None = None
        self._gallery_worker: GalleryWorker | None = None
        self.selected_photos: list[Path] = []
        self.single_photo_before_batch = ""
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(ICON_PATH)) if ICON_PATH.is_file() else QIcon())
        self.setMinimumSize(720, 480)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()
        self._build_menus()
        self._apply_styles()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._start_preview)
        self.frame_path.textChanged.connect(self._schedule_preview)
        self.photo_path.textChanged.connect(self._schedule_preview)
        self.caption.textChanged.connect(self._save_preferences)
        self._schedule_preview()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 18, 28, 24)
        root.setSpacing(18)

        top = QHBoxLayout()
        brand = QVBoxLayout()
        title = QLabel("AutoPost Studio")
        title.setObjectName("appTitle")
        subtitle = QLabel("Compose once. Publish with confidence.")
        subtitle.setObjectName("mutedText")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        top.addLayout(brand)
        top.addStretch(1)
        self.mode_badge = QLabel("CREATOR WORKSPACE")
        self.mode_badge.setObjectName("badge")
        top.addWidget(self.mode_badge, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.workspace_splitter = splitter
        self.workspace_splitter.setHandleWidth(8)
        self.workspace_splitter.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        left = QFrame()
        left.setObjectName("surfaceCard")
        left.setMinimumWidth(0)
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(22, 22, 22, 22)
        left_layout.setSpacing(10)
        left_layout.addWidget(self._heading("Create a post"))
        left_layout.addWidget(self._muted("Choose your frame, image, and export location."))
        left_layout.addSpacing(4)
        self._picker(left_layout, "FRAME OVERLAY", self.frame_path, self._browse_frame)
        self.source_browse_button = self._picker(
            left_layout, "SOURCE IMAGE", self.photo_path, self._browse_photo
        )
        self._picker(left_layout, "OUTPUT FOLDER", self.output_dir, self._browse_output)
        fit_row = QHBoxLayout()
        fit_row.addWidget(self._muted("Photo fit"))
        fit_row.addStretch(1)
        self.fit_box = QComboBox()
        self.fit_box.addItem("Cover", "cover")
        self.fit_box.addItem("Contain", "contain")
        self.fit_box.setCurrentIndex(0 if self.fit == "cover" else 1)
        self.fit_box.currentIndexChanged.connect(lambda: self._fit_changed())
        fit_row.addWidget(self.fit_box)
        left_layout.addLayout(fit_row)
        self.output_format = QComboBox()
        for label, suffix in (("PNG", "png"), ("JPEG", "jpg"), ("WebP", "webp")):
            self.output_format.addItem(label, suffix)
        selected_format = str(self.preferences.get("output_format", "png"))
        format_index = self.output_format.findData(selected_format)
        self.output_format.setCurrentIndex(max(0, format_index))
        self.output_format.currentIndexChanged.connect(lambda: self._format_changed())
        format_row = QHBoxLayout()
        format_row.addWidget(self._muted("Export format"))
        format_row.addStretch(1)
        format_row.addWidget(self.output_format)
        left_layout.addLayout(format_row)
        quality_row = QHBoxLayout()
        quality_row.addWidget(self._muted("JPEG quality"))
        quality_row.addStretch(1)
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        try:
            quality = int(self.preferences.get("quality", 95))
        except (TypeError, ValueError):
            quality = 95
        self.quality.setValue(min(100, max(1, quality)))
        self.quality.setEnabled(self.output_format.currentData() != "png")
        self.quality.valueChanged.connect(self._save_preferences)
        quality_row.addWidget(self.quality)
        left_layout.addLayout(quality_row)
        left_layout.addSpacing(8)
        left_layout.addWidget(self._heading("Caption"))
        self.caption.setPlaceholderText(
            "Write a caption, add keywords, or generate one with your AI provider…"
        )
        self.caption.setMinimumHeight(104)
        left_layout.addWidget(self.caption)
        caption_actions = QHBoxLayout()
        self.generate_button = QPushButton("Generate caption")
        self.generate_button.clicked.connect(self._generate_caption)
        caption_actions.addWidget(self.generate_button)
        caption_actions.addStretch(1)
        left_layout.addLayout(caption_actions)
        action_row = QHBoxLayout()
        self.save_button = QPushButton("Export image")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self._export_one)
        action_row.addWidget(self.save_button)
        left_layout.addLayout(action_row)
        self.operation_progress = QProgressBar()
        self.operation_progress.setTextVisible(False)
        self.operation_progress.setMaximumHeight(5)
        self.operation_progress.setRange(0, 0)
        self.operation_progress.hide()
        left_layout.addWidget(self.operation_progress)
        self.status = QLabel("Ready · load a frame and image to begin")
        self.status.setObjectName("statusText")
        self.status.setWordWrap(True)
        left_layout.addWidget(self.status)
        left_layout.addStretch(1)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left)

        right = QFrame()
        right.setObjectName("surfaceCard")
        right.setMinimumWidth(0)
        right.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(20, 20, 20, 20)
        header = QHBoxLayout()
        header.addWidget(self._heading("Live preview"))
        header.addStretch(1)
        self.preview_info = QLabel("Facebook-ready preview · 3:2 fit")
        self.preview_info.setObjectName("mutedText")
        header.addWidget(self.preview_info)
        right_layout.addLayout(header)
        self.preview_stack = QStackedWidget()
        self.preview = PreviewCanvas()
        self.preview.setMinimumSize(120, 100)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.preview_stack.addWidget(self.preview)
        self.gallery = PreviewGallery()
        self.preview_stack.addWidget(self.gallery)
        right_layout.addWidget(self.preview_stack, 1)
        activity_header = QHBoxLayout()
        activity_header.addWidget(self._heading("Recent exports"))
        activity_header.addStretch(1)
        self.open_scheduler_button = QPushButton("Open scheduler")
        self.open_scheduler_button.clicked.connect(self._show_scheduler)
        activity_header.addWidget(self.open_scheduler_button)
        right_layout.addLayout(activity_header)
        self.activity = QTableWidget(0, 3)
        self.activity.setHorizontalHeaderLabels(["IMAGE", "RESULT", "TIME"])
        self.activity.verticalHeader().setVisible(False)
        self.activity.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.activity.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.activity.horizontalHeader().setStretchLastSection(True)
        self.activity.setMinimumHeight(70)
        self.activity.setMaximumHeight(155)
        right_layout.addWidget(self.activity)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([360, 900])
        root.addWidget(splitter, 1)
        self._render_activity()
        self._update_workspace_layout()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._update_workspace_layout()

    def _update_workspace_layout(self) -> None:
        if not hasattr(self, "workspace_splitter"):
            return
        orientation = (
            Qt.Orientation.Vertical
            if self.width() < 1040
            else Qt.Orientation.Horizontal
        )
        if self.workspace_splitter.orientation() != orientation:
            self.workspace_splitter.setOrientation(orientation)
            if orientation == Qt.Orientation.Vertical:
                self.workspace_splitter.setSizes([360, 700])
            else:
                self.workspace_splitter.setSizes([360, max(500, self.width() - 440)])

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._action(file_menu, "Load Frame…", self._browse_frame)
        self._action(file_menu, "Load Image…", self._browse_photo)
        self._action(file_menu, "Load multiple images…", self._load_multiple)
        self._action(file_menu, "Load image folder…", self._load_image_folder)
        self._action(
            file_menu,
            "Load multiple images [Google Drive]…",
            lambda: QMessageBox.information(
                self, "Google Drive", "Google Drive import is reserved for a future release."
            ),
        )
        file_menu.addSeparator()
        self._action(file_menu, "Post Scheduler…", self._show_scheduler)
        self._action(file_menu, "Add Caption…", self._focus_caption)
        file_menu.addSeparator()
        self._action(file_menu, "Reset preview", self._reset)

        view_menu = self.menuBar().addMenu("&View")
        self.theme_action = QAction("Dark mode", self, checkable=True)
        self.theme_action.setChecked(self.dark_mode)
        self.theme_action.toggled.connect(self._toggle_theme)
        view_menu.addAction(self.theme_action)
        self._action(view_menu, "CLI Console…", self._show_terminal)

        options_menu = self.menuBar().addMenu("&Options")
        self._action(options_menu, "APIs…", self._api_settings)
        self._action(options_menu, "Captions…", self._caption_settings)
        self._action(options_menu, "Scheduler folder…", self._choose_scheduler_folder)

        about_menu = self.menuBar().addMenu("&About")
        self._action(
            about_menu,
            "Developer information",
            self._show_developer_info,
        )
        self._action(
            about_menu,
            "Version / updates",
            lambda: QMessageBox.information(
                self, "AutoPost Studio", f"Version {APP_VERSION}\nUpdates are not yet configured."
            ),
        )

    def _show_developer_info(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Developer information")
        dialog.setMinimumWidth(360)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self._heading("AutoPost Studio"))
        developer = QLabel(
            'Developer: <a href="https://clentindustries.vercel.app/">'
            "ClentIndustries</a>"
        )
        developer.setOpenExternalLinks(True)
        developer.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(developer)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _action(self, menu: Any, label: str, callback: Any) -> QAction:
        action = QAction(label, self)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("heading")
        return label

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("mutedText")
        return label

    def _picker(
        self, layout: QVBoxLayout, title: str, field: QLineEdit, callback: Any
    ) -> QPushButton:
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        row = QHBoxLayout()
        field.setClearButtonEnabled(True)
        field.setMinimumHeight(38)
        browse = QPushButton("Browse")
        browse.clicked.connect(callback)
        row.addWidget(field, 1)
        row.addWidget(browse)
        layout.addWidget(label)
        layout.addLayout(row)
        return browse

    def _apply_styles(self) -> None:
        c = self.colors
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {c['background']}; color: {c['ink']}; font-family: 'Segoe UI'; font-size: 10pt; }}
            QMenuBar {{ background: {c['background']}; padding: 4px; }}
            QMenuBar::item {{ padding: 6px 10px; border-radius: 5px; }}
            QMenuBar::item:selected, QMenu::item:selected {{ background: {c['stage']}; }}
            QMenu {{ background: {c['surface']}; border: 1px solid {c['line']}; padding: 4px; }}
            #appTitle {{ font-size: 25pt; font-weight: 700; }}
            #heading {{ font-size: 12pt; font-weight: 650; }}
            #mutedText, #statusText {{ color: {c['muted']}; }}
            #fieldLabel {{ color: {c['muted']}; font-size: 8pt; font-weight: 700; }}
            #badge {{ color: {c['accent']}; border: 1px solid {c['line']}; border-radius: 12px; padding: 6px 11px; font-size: 8pt; font-weight: 700; }}
            #surfaceCard {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 12px; }}
            QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{ background: {c['field']}; border: 1px solid {c['line']}; border-radius: 7px; padding: 7px 9px; selection-background-color: {c['accent']}; }}
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
            QPushButton {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 7px; padding: 8px 12px; font-weight: 600; }}
            QPushButton:hover {{ border-color: {c['accent']}; color: {c['accent']}; }}
            #primaryButton {{ background: {c['accent']}; color: white; border-color: {c['accent']}; }}
            #primaryButton:hover {{ background: {c['accent_hover']}; color: white; }}
            #previewCanvas {{ background: {c['stage']}; border: 1px solid {c['line']}; border-radius: 9px; color: {c['muted']}; padding: 12px; }}
            #galleryTile {{ background: {c['field']}; border: 1px solid {c['line']}; border-radius: 9px; }}
            #galleryImage {{ background: {c['stage']}; border-radius: 6px; color: {c['muted']}; font-size: 8pt; }}
            #galleryName {{ color: {c['muted']}; font-size: 8pt; }}
            #galleryRemaining {{ color: {c['accent']}; font-weight: 650; }}
            QProgressBar {{ background: {c['stage']}; border: 0; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {c['accent']}; border-radius: 2px; }}
            QTableWidget {{ background: {c['surface']}; alternate-background-color: {c['field']}; border: 1px solid {c['line']}; border-radius: 7px; gridline-color: {c['line']}; }}
            QHeaderView::section {{ background: {c['field']}; color: {c['muted']}; border: 0; padding: 7px; font-size: 8pt; font-weight: 700; }}
            #terminalOutput {{ background: #0b1220; color: #d4f5dd; border: 1px solid #283547; border-radius: 8px; padding: 12px; font-family: Consolas, monospace; font-size: 10pt; selection-background-color: #264f78; }}
        """)

    def _browse_frame(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a transparent frame", self.frame_path.text(), "PNG images (*.png);;" + IMAGE_FILTER
        )
        if path:
            self.frame_path.setText(path)
            self._save_preferences()

    def _browse_photo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an image", self.photo_path.text(), IMAGE_FILTER
        )
        if path:
            self._clear_batch_selection()
            self.photo_path.setText(path)
            self._save_preferences()

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self.output_dir.text()
        )
        if folder:
            self.output_dir.setText(folder)
            self._save_preferences()

    def _fit_changed(self) -> None:
        self.fit = str(self.fit_box.currentData())
        self._save_preferences()
        self._schedule_preview()

    def _format_changed(self) -> None:
        self.quality.setEnabled(self.output_format.currentData() != "png")
        self._save_preferences()

    def _schedule_preview(self, *_: Any) -> None:
        self._preview_generation += 1
        self._timer.start()

    def _start_preview(self) -> None:
        frame = Path(self.frame_path.text()).expanduser()
        if self.selected_photos:
            if not frame.is_file():
                self.gallery.show_loading(len(self.selected_photos))
                self.preview_info.setText("Load a frame to preview the selected images")
                return
            generation = self._preview_generation
            worker = GalleryWorker(
                generation, frame, self.selected_photos, self.fit
            )
            worker.ready.connect(self._gallery_ready)
            worker.failed.connect(self._gallery_failed)
            self._gallery_worker = worker
            self._run_worker(worker)
            return
        photo = Path(self.photo_path.text()).expanduser()
        if not frame.is_file() or not photo.is_file():
            self.preview.clear_image("Load a frame and an image to preview your post")
            self.preview_stack.setCurrentWidget(self.preview)
            return
        self.preview_stack.setCurrentWidget(self.preview)
        generation = self._preview_generation
        worker = PreviewWorker(generation, frame, photo, self.fit)
        worker.ready.connect(self._preview_ready)
        worker.failed.connect(self._preview_failed)
        self._run_worker(worker)

    def _run_worker(self, worker: QThread) -> None:
        self._workers.add(worker)
        worker.finished.connect(lambda current=worker: self._workers.discard(current))
        worker.start()

    def _set_busy(self, busy: bool, current: int = 0, total: int = 0) -> None:
        if busy:
            self.operation_progress.show()
            if total:
                self.operation_progress.setRange(0, total)
                self.operation_progress.setValue(current)
            else:
                self.operation_progress.setRange(0, 0)
            QApplication.processEvents()
            return
        self.operation_progress.hide()
        self.operation_progress.setRange(0, 1)
        self.operation_progress.setValue(0)

    def _preview_ready(
        self, generation: int, image: Image.Image, size: object
    ) -> None:
        if generation != self._preview_generation:
            return
        self.preview.set_image(image)
        width, height = size
        self.preview_info.setText(
            f"Facebook upload preview · {width} × {height} px"
        )
        self.status.setText("Preview is ready")

    def _preview_failed(self, generation: int, error: str) -> None:
        if generation != self._preview_generation:
            return
        self.preview.clear_image("Could not load the selected image or frame")
        self.status.setText(error)
        self.log.error("Preview failed: %s", error)

    def _gallery_ready(
        self, generation: int, previews: list[tuple[str, QImage | None]]
    ) -> None:
        if generation != self._preview_generation or not self.selected_photos:
            return
        self.gallery.set_previews(previews, len(self.selected_photos))
        self.preview_stack.setCurrentWidget(self.gallery)
        self.preview_info.setText(
            f"{len(self.selected_photos)} selected · showing {min(8, len(self.selected_photos))}"
        )
        self.status.setText(f"{len(self.selected_photos)} images ready to export")

    def _gallery_failed(self, generation: int, error: str) -> None:
        if generation != self._preview_generation:
            return
        self.log.error("Batch preview failed: %s", error)
        self.preview_info.setText("Batch preview unavailable")
        self.status.setText(error)

    def _export_one(self) -> None:
        if self.selected_photos:
            self._start_batch(self.selected_photos)
            return
        self._export_single()

    def _export_single(self) -> None:
        frame = Path(self.frame_path.text()).expanduser()
        photo = Path(self.photo_path.text()).expanduser()
        if not frame.is_file() or not photo.is_file():
            QMessageBox.warning(self, "Images required", "Select a valid frame and image.")
            return
        output_dir = Path(self.output_dir.text()).expanduser()
        output = output_dir / f"{photo.stem}_framed.{self.output_format.currentData()}"
        self._set_busy(True, 0, 0)
        self.save_button.setEnabled(False)
        self.save_button.setText("Exporting…")
        QApplication.processEvents()
        try:
            composed = attach_frame(load_frame(frame), load_photo(photo), self.fit)
            save_image(composed, output, self.quality.value())
            save_post(
                PostRecord(
                    image=str(output.resolve()),
                    caption=self.caption.toPlainText().strip(),
                    page_id=str(self.preferences.get("facebook_page_id", "")),
                ),
                self._scheduler_directory(),
            )
            self._record_export(photo, frame, output)
            self.status.setText(f"Exported {output.name} · post metadata saved")
            self._render_activity()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            self.log.exception("Export failed")
            QMessageBox.critical(self, "Export failed", str(exc))
        finally:
            self.save_button.setEnabled(True)
            self.save_button.setText("Export image")
            self._set_busy(False)

    def _load_multiple(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose images", "", IMAGE_FILTER
        )
        if paths:
            self._set_selected_photos([Path(path) for path in paths])

    def _load_image_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose image folder")
        if not folder:
            return
        try:
            photos = sorted(
                p for p in Path(folder).iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
            )
        except OSError as exc:
            QMessageBox.critical(self, "Could not read folder", str(exc))
            return
        if not photos:
            QMessageBox.information(self, "No images found", "No supported images were found in this folder.")
            return
        self._set_selected_photos(photos)

    def _set_selected_photos(self, photos: list[Path]) -> None:
        if not self.selected_photos:
            self.single_photo_before_batch = self.photo_path.text()
        self.selected_photos = photos
        self.photo_path.clear()
        self.photo_path.setPlaceholderText("Unavailable while multiple images are selected")
        self.photo_path.setEnabled(False)
        self.source_browse_button.setEnabled(False)
        self.preview_stack.setCurrentWidget(self.gallery)
        self.gallery.show_loading(len(photos))
        self.save_button.setText(f"Export {len(photos)} images")
        self._schedule_preview()

    def _clear_batch_selection(self) -> None:
        if not self.selected_photos:
            return
        self.selected_photos = []
        self.photo_path.setEnabled(True)
        self.photo_path.setPlaceholderText("")
        self.source_browse_button.setEnabled(True)
        self.photo_path.setText(self.single_photo_before_batch)
        self.single_photo_before_batch = ""
        self.save_button.setText("Export image")
        self.preview_stack.setCurrentWidget(self.preview)
        self._schedule_preview()

    def _start_batch(self, photos: list[Path]) -> None:
        frame = Path(self.frame_path.text()).expanduser()
        if not frame.is_file():
            QMessageBox.warning(self, "Frame required", "Load a frame before exporting images.")
            return
        self.save_button.setEnabled(False)
        self.generate_button.setEnabled(False)
        self._set_busy(True, 0, len(photos))
        worker = BatchWorker(
            frame,
            photos,
            Path(self.output_dir.text()).expanduser(),
            self.fit,
            self.quality.value(),
            str(self.output_format.currentData()),
            self.caption.toPlainText().strip(),
            str(self.preferences.get("facebook_page_id", "")),
            self._scheduler_directory(),
        )
        worker.progress.connect(self._batch_progress)
        worker.complete.connect(self._batch_complete)
        worker.failed.connect(self._batch_failed)
        self._batch = worker
        self._batch_frame = frame
        self._run_worker(worker)

    def _batch_progress(self, current: int, total: int, name: str) -> None:
        self.operation_progress.setRange(0, max(1, total))
        self.operation_progress.setValue(current)
        self.status.setText(f"Framing {current}/{total} · {name}")

    def _batch_complete(
        self, succeeded: int, total: int, outputs: list[tuple[Path, Path]]
    ) -> None:
        self.save_button.setEnabled(True)
        self.generate_button.setEnabled(True)
        self._batch = None
        self._set_busy(False)
        frame = self._batch_frame or Path(self.frame_path.text()).expanduser()
        for photo, output in outputs:
            self._record_export(photo, frame, output)
        self._batch_frame = None
        self.status.setText(f"Batch finished · {succeeded} of {total} images exported")
        if succeeded != total:
            QMessageBox.warning(
                self, "Batch completed with errors", f"{succeeded} of {total} images exported. See the log for skipped images."
            )
        else:
            QMessageBox.information(self, "Batch complete", f"Exported {succeeded} images.")
        self._render_activity()

    def _batch_failed(self, error: str) -> None:
        self.save_button.setEnabled(True)
        self.generate_button.setEnabled(True)
        self._batch = None
        self._batch_frame = None
        self._set_busy(False)
        self.log.error("Batch export failed: %s", error)
        self.status.setText("Batch export failed")
        QMessageBox.critical(self, "Batch export failed", error)

    def _record_export(self, photo: Path, frame: Path, output: Path) -> None:
        try:
            record_activity(
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "photo": str(photo),
                    "frame": str(frame),
                    "output": str(output),
                    "status": "success",
                }
            )
        except OSError:
            self.log.exception("Could not record export activity")

    def _render_activity(self) -> None:
        entries = list(reversed(load_activity()[-5:]))
        self.activity.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            timestamp = str(entry.get("timestamp", ""))
            try:
                when = datetime.fromisoformat(timestamp).strftime("%H:%M")
            except ValueError:
                when = "—"
            values = (
                Path(str(entry.get("output", entry.get("photo", "Image")))).name,
                "Exported" if entry.get("status") == "success" else "Failed",
                when,
            )
            for col, value in enumerate(values):
                self.activity.setItem(row, col, QTableWidgetItem(value))
        self.activity.resizeColumnsToContents()

    def _show_scheduler(self) -> None:
        folder = self._scheduler_directory()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            SchedulerDialog(
                folder,
                self,
                str(self.preferences.get("facebook_page_id", "")),
            ).exec()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Scheduler unavailable", str(exc))

    def _scheduler_directory(self) -> Path:
        return Path(
            str(self.preferences.get("scheduler_dir") or self.output_dir.text() or OUTPUT_DIR)
        ).expanduser()

    def _choose_scheduler_folder(self) -> None:
        current = str(
            self.preferences.get("scheduler_dir") or self.output_dir.text() or OUTPUT_DIR
        )
        folder = QFileDialog.getExistingDirectory(self, "Choose scheduler folder", current)
        if folder:
            self.preferences["scheduler_dir"] = folder
            self._save_preferences()
            QMessageBox.information(self, "Scheduler folder", f"Scheduler now reads:\n{folder}")

    def _reset(self) -> None:
        self._clear_batch_selection()
        self.frame_path.clear()
        self.photo_path.clear()
        self.caption.clear()
        self.preview.clear_image("Load a frame and an image to preview your post")
        self.preview_stack.setCurrentWidget(self.preview)
        self.preview_info.setText("Facebook-ready preview · 3:2 fit")
        self.status.setText("Preview reset")
        self._save_preferences()

    def _focus_caption(self) -> None:
        self.caption.setFocus()
        self.status.setText("Write or edit your post caption in the composer.")

    def _toggle_theme(self, enabled: bool) -> None:
        self.dark_mode = enabled
        self.colors = dict(DARK_COLORS if enabled else COLORS)
        self._apply_styles()
        self._save_preferences()

    def _show_terminal(self) -> None:
        dialog = TerminalDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def _api_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("API settings")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        page_id = QLineEdit(str(self.preferences.get("facebook_page_id", "")))
        page_id.setPlaceholderText("Your Facebook Page ID")
        page_token = QLineEdit()
        page_token.setEchoMode(QLineEdit.EchoMode.Password)
        page_token.setPlaceholderText(
            "System User token recommended; stored in Windows Credential Manager"
        )
        try:
            page_token.setText(get_secret("facebook_page_token"))
        except RuntimeError as exc:
            QMessageBox.critical(self, "Credential vault unavailable", str(exc))
            return
        form.addRow("Facebook Page ID", page_id)
        form.addRow("System User / Page token", page_token)
        layout.addLayout(form)
        note = self._muted(
            "For a business-owned Page, create a System User in Meta Business Settings, "
            "assign the app and Page with content-creation access, then generate a token "
            "with the required Pages permissions. Never request publish_actions. "
            "Tokens are stored in Windows Credential Manager; this does not prevent Meta "
            "from revoking a token."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        connection_status = QLabel("")
        connection_status.setWordWrap(True)
        layout.addWidget(connection_status)
        test_button = QPushButton("Test Page access")

        def test_page_access() -> None:
            test_button.setEnabled(False)
            test_button.setText("Testing…")
            self._set_busy(True)
            try:
                name = validate_page_access(
                    page_id.text().strip(), page_token.text().strip()
                )
            except (FacebookError, ValueError) as exc:
                connection_status.setText(f"Connection failed: {exc}")
                connection_status.setStyleSheet(
                    f"color: {self.colors['danger']};"
                )
            else:
                connection_status.setText(
                    f"Connected to Page: {name}. This confirms Page access, "
                    "but does not test publish permissions."
                )
                connection_status.setStyleSheet(f"color: {self.colors['success']};")
            finally:
                test_button.setEnabled(True)
                test_button.setText("Test Page access")
                self._set_busy(False)

        test_button.clicked.connect(test_page_access)
        layout.addWidget(test_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.preferences["facebook_page_id"] = page_id.text().strip()
                if page_token.text().strip():
                    set_secret("facebook_page_token", page_token.text().strip())
                self._save_preferences()
            except RuntimeError as exc:
                QMessageBox.critical(self, "Credential vault unavailable", str(exc))

    def _caption_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Caption assistant")
        form = QFormLayout(dialog)
        provider = QComboBox()
        for provider_id, config in PROVIDER_DEFAULTS.items():
            provider.addItem(str(config["label"]), provider_id)
        selected_provider = str(self.preferences.get("caption_provider", "groq"))
        if selected_provider not in PROVIDER_DEFAULTS:
            selected_provider = "groq"
        provider.setCurrentIndex(provider.findData(selected_provider))
        saved_models = self.preferences.get("caption_models", {})
        saved_endpoints = self.preferences.get("caption_endpoints", {})
        model_values = {
            provider_id: str(
                saved_models.get(
                    provider_id,
                    self.preferences.get("caption_model", config["model"])
                    if provider_id == selected_provider
                    else config["model"],
                )
            )
            for provider_id, config in PROVIDER_DEFAULTS.items()
        } if isinstance(saved_models, dict) else {
            provider_id: str(config["model"])
            for provider_id, config in PROVIDER_DEFAULTS.items()
        }
        endpoint_values = {
            provider_id: str(
                saved_endpoints.get(
                    provider_id,
                    self.preferences.get("caption_endpoint", config["endpoint"])
                    if provider_id == selected_provider
                    else config["endpoint"],
                )
            )
            for provider_id, config in PROVIDER_DEFAULTS.items()
        } if isinstance(saved_endpoints, dict) else {
            provider_id: str(config["endpoint"])
            for provider_id, config in PROVIDER_DEFAULTS.items()
        }
        api_key_values: dict[str, str] = {}
        try:
            for provider_id in PROVIDER_DEFAULTS:
                api_key_values[provider_id] = get_secret(
                    f"caption_api_key_{provider_id}"
                )
            if not api_key_values["groq"]:
                api_key_values["groq"] = get_secret("caption_api_key")
        except RuntimeError as exc:
            QMessageBox.critical(self, "Credential vault unavailable", str(exc))
            return
        endpoint = QLineEdit()
        model = QLineEdit()
        keywords = QLineEdit(str(self.preferences.get("caption_keywords", "")))
        template = QLineEdit(
            str(self.preferences.get("caption_template", "{caption}\n\n{hashtags}"))
        )
        api_key = QLineEdit()
        api_key.setEchoMode(QLineEdit.EchoMode.Password)
        active_provider = selected_provider

        def show_provider(provider_id: str, save_current: bool = False) -> None:
            nonlocal active_provider
            if save_current:
                model_values[active_provider] = model.text()
                endpoint_values[active_provider] = endpoint.text()
                api_key_values[active_provider] = api_key.text()
            active_provider = provider_id
            config = PROVIDER_DEFAULTS[provider_id]
            model.setText(model_values[provider_id])
            endpoint.setText(endpoint_values[provider_id])
            endpoint.setReadOnly(provider_id in {"groq", "gemini"})
            endpoint.setToolTip(
                "Managed by the official SDK."
                if provider_id in {"groq", "gemini"}
                else ""
            )
            api_key.setText(api_key_values[provider_id])
            requires_key = bool(config["requires_api_key"])
            api_key.setEnabled(requires_key)
            api_key.setPlaceholderText(
                f"{config['label']} API key"
                if requires_key
                else "Not required for local Ollama"
            )

        provider.currentIndexChanged.connect(
            lambda: show_provider(str(provider.currentData()), save_current=True)
        )
        form.addRow("Provider", provider)
        form.addRow("Endpoint", endpoint)
        form.addRow("Model", model)
        form.addRow("Keywords", keywords)
        form.addRow("Caption template", template)
        form.addRow("API key", api_key)
        show_provider(selected_provider)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            active_provider = str(provider.currentData())
            model_values[active_provider] = model.text().strip()
            endpoint_values[active_provider] = endpoint.text().strip()
            api_key_values[active_provider] = api_key.text().strip()
            self.preferences.update(
                {
                    "caption_provider": active_provider,
                    "caption_models": model_values,
                    "caption_endpoints": endpoint_values,
                    "caption_model": model_values[active_provider],
                    "caption_keywords": keywords.text().strip(),
                    "caption_template": template.text().strip(),
                }
            )
            try:
                for provider_id, key in api_key_values.items():
                    if key:
                        set_secret(f"caption_api_key_{provider_id}", key)
                if active_provider == "groq" and api_key_values["groq"]:
                    set_secret("caption_api_key", api_key_values["groq"])
                self._save_preferences()
            except RuntimeError as exc:
                QMessageBox.critical(self, "Credential vault unavailable", str(exc))

    def _generate_caption(self) -> None:
        self.generate_button.setEnabled(False)
        self.generate_button.setText("Generating…")
        self._set_busy(True)
        try:
            result = generate_caption(
                self.preferences,
                self._caption_api_key(),
                self.caption.toPlainText(),
            )
            self.caption.setPlainText(result)
        except (ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Caption generation failed", str(exc))
        finally:
            self.generate_button.setEnabled(True)
            self.generate_button.setText("Generate caption")
            self._set_busy(False)

    def _caption_api_key(self) -> str:
        provider = str(self.preferences.get("caption_provider", "groq"))
        try:
            key = get_secret(f"caption_api_key_{provider}")
            if not key and provider == "groq":
                key = get_secret("caption_api_key")
            return key
        except RuntimeError as exc:
            raise RuntimeError(f"Credential vault unavailable: {exc}") from exc

    def _save_preferences(self, *_: Any) -> None:
        self.preferences.update(
            {
                "frame_path": self.frame_path.text(),
                "photo_path": (
                    self.single_photo_before_batch
                    if self.selected_photos
                    else self.photo_path.text()
                ),
                "output_dir": self.output_dir.text(),
                "fit": self.fit,
                "quality": self.quality.value() if hasattr(self, "quality") else 95,
                "output_format": (
                    self.output_format.currentData()
                    if hasattr(self, "output_format")
                    else "png"
                ),
                "caption": self.caption.toPlainText() if hasattr(self, "caption") else "",
                "dark_mode": self.dark_mode,
            }
        )
        try:
            save_json(APP_DIR / "data" / "preferences.json", self.preferences)
        except OSError:
            self.log.exception("Could not save preferences")


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    try:
        _launch_scheduler_service()
    except OSError as exc:
        logging.getLogger("frame_studio").exception(
            "Could not start scheduler background worker"
        )
        QMessageBox.critical(
            None,
            "Scheduler service could not start",
            f"Scheduled posts will not run while the app is closed.\n\n{exc}",
        )
    window = AutoPostStudio()
    window.showMaximized()
    app.exec()


def _launch_scheduler_service() -> None:
    if _is_frozen_runtime():
        command = [sys.executable, "--scheduler-worker"]
    else:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            "--scheduler-worker",
        ]
    options: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if platform.system().casefold() == "windows":
        options["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


def _is_frozen_runtime() -> bool:
    return bool(vars(sys).get("frozen", False))
