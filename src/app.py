"""AutoPost Studio desktop application."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from PySide6.QtCore import (
    QDate,
    QDateTime,
    QEvent,
    QPoint,
    QPointF,
    QProcess,
    QRect,
    QTime,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QImage,
    QPalette,
    QPainter,
    QPen,
    QPixmap,
    QTextCharFormat,
    QWheelEvent,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGestureEvent,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox as QtMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPinchGesture,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyleFactory,
    QTableWidget,
    QTableWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .captions import generate_caption
from .captions import PROVIDER_DEFAULTS
from .config import (
    APP_INSTALL_DIR,
    APP_NAME,
    APP_VERSION,
    DEFAULT_FRAME_PATH,
    GCASH_QR_PATH,
    ICON_PATH,
    PREFERENCES_PATH,
)
from .facebook import FacebookError, post_album, post_photo, validate_page_access
from .google_drive import (
    DRIVE_TOKEN_SECRET,
    DriveFile,
    GoogleDriveError,
    download_drive_images,
    list_drive_items,
    validate_client_secrets,
)
from .imaging import (
    SUPPORTED_EXTS,
    attach_frame,
    load_frame,
    load_photo,
    process_one,
    process_without_frame,
    save_image,
)
from .scheduler import PostRecord, delete_post_files, load_posts, save_post
from .storage import (
    ACTIVITY_PATH,
    OUTPUT_DIR,
    configure_logging,
    load_activity,
    load_preferences,
    record_activity,
    save_json,
)
from .vault import delete_secret, get_secret, set_secret

META_MIN_SCHEDULE_SECONDS = 10 * 60
META_MAX_SCHEDULE_SECONDS = 30 * 24 * 60 * 60
GALLERY_THUMBNAIL_SIZE = (320, 240)


def _pil_image_to_qimage(
    image: Image.Image,
    thumbnail_size: tuple[int, int] | None = None,
) -> QImage:
    """Convert a Pillow image to an owned Qt image, optionally thumbnailing it."""
    rendered = image.convert("RGBA")
    if thumbnail_size is not None:
        rendered.thumbnail(thumbnail_size, Image.Resampling.LANCZOS)
    return QImage(
        rendered.tobytes("raw", "RGBA"),
        rendered.width,
        rendered.height,
        rendered.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()


def _facebook_page_secret_name(page_id: str) -> str:
    return f"facebook_page_token_{page_id}"


def _configured_facebook_pages(
    preferences: dict[str, Any],
) -> list[dict[str, str]]:
    saved_pages = preferences.get("facebook_pages")
    pages: list[dict[str, str]] = []
    seen_page_ids: set[str] = set()
    if isinstance(saved_pages, list):
        for value in saved_pages:
            if not isinstance(value, dict):
                continue
            page_id = str(value.get("page_id", "")).strip()
            name = str(value.get("name", "")).strip()
            if page_id and page_id not in seen_page_ids:
                pages.append({"name": name or page_id, "page_id": page_id})
                seen_page_ids.add(page_id)
    elif preferences.get("facebook_page_id"):
        page_id = str(preferences["facebook_page_id"]).strip()
        if page_id:
            pages.append({"name": page_id, "page_id": page_id})
    return pages


def _validate_meta_schedule(schedule_at: datetime) -> None:
    seconds_until_schedule = (schedule_at - datetime.now()).total_seconds()
    if not (
        META_MIN_SCHEDULE_SECONDS <= seconds_until_schedule <= META_MAX_SCHEDULE_SECONDS
    ):
        raise ValueError(
            "Meta accepts scheduled posts from 10 minutes to 30 days in advance."
        )


COLORS = {
    "background": "#f5f6f8",
    "surface": "#ffffff",
    "ink": "#172033",
    "muted": "#687386",
    "line": "#e3e7ee",
    "accent": "#4f62d8",
    "accent_hover": "#4052c2",
    "stage": "#eef1f6",
    "success": "#15803d",
    "danger": "#b42318",
    "field": "#f8f9fb",
    "hover": "#f1f3f7",
}
DARK_COLORS = {
    "background": "#090909",
    "surface": "#111111",
    "ink": "#f2f2f2",
    "muted": "#a0a0a0",
    "line": "#292929",
    "accent": "#8294ff",
    "accent_hover": "#9ba9ff",
    "stage": "#080808",
    "success": "#65d98b",
    "danger": "#ff7777",
    "field": "#171717",
    "hover": "#202020",
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
        self,
        generation: int,
        frame_path: Path | None,
        photo_path: Path,
        fit: str,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.frame_path = frame_path
        self.photo_path = photo_path
        self.fit = fit

    def run(self) -> None:
        try:
            photo = load_photo(self.photo_path)
            composite = (
                attach_frame(load_frame(self.frame_path), photo, self.fit)
                if self.frame_path is not None
                else photo
            )
            self.ready.emit(self.generation, composite, composite.size)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            self.failed.emit(self.generation, str(exc))


class GalleryWorker(QThread):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(
        self,
        generation: int,
        frame_path: Path | None,
        photos: list[Path],
        fit: str,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.frame_path = frame_path
        self.photos = photos
        self.fit = fit

    def run(self) -> None:
        try:
            frame = load_frame(self.frame_path) if self.frame_path is not None else None
            previews: list[tuple[str, QImage | None]] = []
            for photo_path in self.photos:
                try:
                    photo = load_photo(photo_path)
                    composite = (
                        attach_frame(frame, photo, self.fit)
                        if frame is not None
                        else photo
                    )
                    qimage = _pil_image_to_qimage(composite, GALLERY_THUMBNAIL_SIZE)
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
        frame_path: Path | None,
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
            frame = load_frame(self.frame_path) if self.frame_path is not None else None
            succeeded = 0
            outputs: list[tuple[Path, Path]] = []
            for index, photo in enumerate(self.photos, 1):
                output = self.output_dir / (
                    f"{photo.stem}_{index:03d}_"
                    f"{'framed' if frame is not None else 'post'}."
                    f"{self.image_format}"
                )
                ok = (
                    process_one(
                        frame,
                        photo,
                        output,
                        self.fit,
                        (0.5, 0.5),
                        self.quality,
                    )
                    if frame is not None
                    else process_without_frame(photo, output, self.quality)
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
        tokens: str | dict[str, str],
        scheduled: bool,
        schedule_times: list[datetime | None],
        album: bool,
    ) -> None:
        super().__init__()
        self.records = records
        self.tokens = (
            tokens
            if isinstance(tokens, dict)
            else {record.page_id: tokens for record in records}
        )
        self.scheduled = scheduled
        self.schedule_times = schedule_times
        self.album = album

    def run(self) -> None:
        results: list[tuple[int, dict[str, Any]]] = []
        failure = ""
        try:
            if self.album:
                self.progress.emit(0, 0, "Uploading album to Facebook…")
                result = post_album(
                    self.records[0].page_id,
                    self.tokens[self.records[0].page_id],
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
                        f"{'Scheduling' if self.scheduled else 'Publishing'} "
                        f"{index} of {total} · {Path(record.image).name}",
                    )
                    result = post_photo(
                        record.page_id,
                        self.tokens[record.page_id],
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


class DriveListingWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        client_secrets_path: Path,
        folder_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client_secrets_path = client_secrets_path
        self.folder_id = folder_id

    def run(self) -> None:
        try:
            self.ready.emit(
                list_drive_items(self.client_secrets_path, self.folder_id)
            )
        except GoogleDriveError as exc:
            self.failed.emit(str(exc))


class DriveDownloadWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        client_secrets_path: Path,
        files: list[DriveFile],
        destination: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client_secrets_path = client_secrets_path
        self.files = files
        self.destination = destination

    def run(self) -> None:
        try:
            self.ready.emit(
                download_drive_images(
                    self.client_secrets_path, self.files, self.destination
                )
            )
        except GoogleDriveError as exc:
            self.failed.emit(str(exc))


class PreviewGallery(QWidget):
    """Scrollable, selectable thumbnails for every photo in a batch."""

    zoomRequested = Signal(int)
    selectionChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        help_text = QLabel(
            "Select a photo to inspect it. Double-click a row or use Zoom selected "
            "to view the full-size composition."
        )
        help_text.setObjectName("mutedText")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.items = QListWidget()
        self.items.setIconSize(QSize(160, 110))
        self.items.setSpacing(4)
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.items.currentRowChanged.connect(self.selectionChanged)
        self.items.itemDoubleClicked.connect(self._zoom_item)
        layout.addWidget(self.items, 1)
        footer = QHBoxLayout()
        self.selection_info = QLabel("No photos loaded")
        self.selection_info.setObjectName("galleryRemaining")
        footer.addWidget(self.selection_info)
        footer.addStretch(1)
        self.zoom_button = QPushButton("Zoom selected")
        self.zoom_button.setEnabled(False)
        self.zoom_button.clicked.connect(self.zoom_selected)
        footer.addWidget(self.zoom_button)
        layout.addLayout(footer)

    def set_previews(
        self,
        previews: list[tuple[str, QImage | None]],
    ) -> None:
        selected_row = self.items.currentRow()
        self.items.clear()
        for filename, qimage in previews:
            label = filename
            if qimage is None:
                label = f"{filename} — preview unavailable"
            item = QListWidgetItem(label)
            item.setToolTip(filename)
            if qimage is not None:
                item.setIcon(QIcon(QPixmap.fromImage(qimage)))
            self.items.addItem(item)
        if previews:
            self.items.setCurrentRow(min(max(0, selected_row), len(previews) - 1))
        else:
            self.selection_info.setText("No photos loaded")
            self.zoom_button.setEnabled(False)

    def show_loading(self, photos: list[Path]) -> None:
        self.items.clear()
        for photo in photos:
            item = QListWidgetItem(f"{photo.name} — preparing preview…")
            item.setToolTip(str(photo))
            self.items.addItem(item)
        if photos:
            self.items.setCurrentRow(0)
        self.selection_info.setText(f"Preparing {len(photos)} photo(s)…")
        self.zoom_button.setEnabled(False)

    def set_selection_info(self, index: int, total: int) -> None:
        self.selection_info.setText(
            f"Photo {index + 1} of {total}"
            if 0 <= index < total
            else "No photo selected"
        )
        self.zoom_button.setEnabled(0 <= index < total)

    def zoom_selected(self) -> None:
        index = self.items.currentRow()
        if index >= 0:
            self.zoomRequested.emit(index)

    def _zoom_item(self, item: QListWidgetItem) -> None:
        index = self.items.row(item)
        if index >= 0:
            self.zoomRequested.emit(index)


class PreviewZoomDialog(QDialog):
    """Full-resolution composition viewer with adjustable zoom."""

    def __init__(
        self, title: str, image: Image.Image, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Preview · {title}")
        screen = self.screen()
        available = screen.availableGeometry() if screen else self.geometry()
        self.resize(
            min(1100, max(520, round(available.width() * 0.85))),
            min(820, max(360, round(available.height() * 0.85))),
        )
        self.source = QPixmap.fromImage(_pil_image_to_qimage(image))

        layout = QVBoxLayout(self)
        self.scroll_area = PreviewZoomArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidget(self.image_label)
        self.scroll_area.zoomRequested.connect(self._zoom_by)
        layout.addWidget(self.scroll_area, 1)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Ctrl + scroll to zoom · touchpad pinch to zoom"))
        controls.addStretch(1)
        self.zoom_value = QLabel("")
        controls.addWidget(self.zoom_value)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        fit_ratio = min(
            (self.width() - 70) / max(1, self.source.width()),
            (self.height() - 130) / max(1, self.source.height()),
            1.0,
        )
        self._zoom_factor = max(0.1, fit_ratio)
        self._apply_zoom()

    def _zoom_by(self, factor: float, anchor: QPointF) -> None:
        old_factor = self._zoom_factor
        new_factor = min(8.0, max(0.1, old_factor * factor))
        if new_factor == old_factor:
            return

        viewport = self.scroll_area.viewport()
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        anchor_x = anchor.x()
        anchor_y = anchor.y()
        old_origin_x = max(
            0.0, (viewport.width() - self.source.width() * old_factor) / 2
        )
        old_origin_y = max(
            0.0, (viewport.height() - self.source.height() * old_factor) / 2
        )
        source_x = (horizontal.value() + anchor_x - old_origin_x) / old_factor
        source_y = (vertical.value() + anchor_y - old_origin_y) / old_factor
        self._zoom_factor = new_factor
        self._apply_zoom()
        new_origin_x = max(
            0.0, (viewport.width() - self.source.width() * new_factor) / 2
        )
        new_origin_y = max(
            0.0, (viewport.height() - self.source.height() * new_factor) / 2
        )
        horizontal.setValue(round(source_x * new_factor + new_origin_x - anchor_x))
        vertical.setValue(round(source_y * new_factor + new_origin_y - anchor_y))

    def _apply_zoom(self) -> None:
        percent = round(self._zoom_factor * 100)
        size = QSize(
            max(1, round(self.source.width() * self._zoom_factor)),
            max(1, round(self.source.height() * self._zoom_factor)),
        )
        pixmap = self.source.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(pixmap)
        self.image_label.resize(pixmap.size())
        self.zoom_value.setText(f"{percent}%")


class PreviewZoomArea(QScrollArea):
    """Scroll the preview normally; Ctrl+wheel and pinch gestures zoom it."""

    zoomRequested = Signal(float, object)

    def __init__(self) -> None:
        super().__init__()
        self.grabGesture(Qt.GestureType.PinchGesture)
        self.viewport().grabGesture(Qt.GestureType.PinchGesture)

    def _handle_pinch(self, event: QEvent) -> bool:
        if not isinstance(event, QGestureEvent):
            return False
        gesture = event.gesture(Qt.GestureType.PinchGesture)
        if not isinstance(gesture, QPinchGesture):
            return False
        factor = gesture.scaleFactor()
        if factor <= 0 or factor == 1.0:
            return False
        self.zoomRequested.emit(factor, gesture.centerPoint())
        event.accept(gesture)
        return True

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return
        delta = event.pixelDelta().y()
        if not delta:
            delta = event.angleDelta().y()
        if delta:
            factor = 1.15 ** (delta / 120)
            self.zoomRequested.emit(factor, event.position())
            event.accept()
            return
        event.ignore()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Gesture and self._handle_pinch(event):
            return True
        return super().event(event)

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Gesture and self._handle_pinch(event):
            return True
        return super().viewportEvent(event)


class PreviewCanvas(QLabel):
    inspectRequested = Signal()

    def __init__(self) -> None:
        super().__init__("Load a frame and an image to preview your post")
        self.setObjectName("previewCanvas")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source: QPixmap | None = None

    def set_image(self, image: Image.Image) -> None:
        self._source = QPixmap.fromImage(_pil_image_to_qimage(image))
        self._rescale()

    def clear_image(self, message: str) -> None:
        self._source = None
        self.setPixmap(QPixmap())
        self.setText(message)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if self._source is not None:
            self.inspectRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

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


class MonthOnlyCalendar(QCalendarWidget):
    """Calendar that leaves adjacent-month cells blank."""

    def paintCell(self, painter: QPainter, rect: QRect, date: QDate) -> None:
        if date.year() != self.yearShown() or date.month() != self.monthShown():
            painter.fillRect(rect, self.palette().color(QPalette.ColorRole.Base))
            return
        super().paintCell(painter, rect, date)


class CalendarDateTimeEdit(QDateTimeEdit):
    """Date/time editor with an explicit calendar toggle button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.calendar_button = QToolButton(self)
        self.calendar_button.setObjectName("calendarToggleButton")
        self.calendar_button.setToolTip("Open calendar")
        self.calendar_button.setAccessibleName("Open calendar")
        self.calendar_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.calendar_button.clicked.connect(self._toggle_calendar)
        self.calendar: MonthOnlyCalendar | None = None

    def set_month_calendar(self, calendar: MonthOnlyCalendar) -> None:
        self.calendar = calendar
        self.calendar.clicked.connect(self._calendar_date_selected)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        button_size = min(28, max(22, self.height() - 6))
        self.calendar_button.setGeometry(
            self.width() - button_size - 5,
            (self.height() - button_size) // 2,
            button_size,
            button_size,
        )

    def _toggle_calendar(self) -> None:
        if self.calendar is None:
            return
        if self.calendar.isVisible():
            self.calendar.hide()
            return
        selected_date = self.dateTime().date()
        self.calendar.setDateRange(self.minimumDate(), self.maximumDate())
        self.calendar.setSelectedDate(selected_date)
        self.calendar.setCurrentPage(selected_date.year(), selected_date.month())
        self.calendar.adjustSize()
        anchor = self.calendar_button.mapToGlobal(
            QPoint(0, self.calendar_button.height())
        )
        screen = self.screen()
        available = screen.availableGeometry() if screen else self.geometry()
        popup_x = min(
            max(
                available.left(),
                anchor.x() + self.calendar_button.width() - self.calendar.width(),
            ),
            available.right() - self.calendar.width() + 1,
        )
        popup_y = anchor.y()
        if popup_y + self.calendar.height() > available.bottom() + 1:
            popup_y = (
                self.calendar_button.mapToGlobal(QPoint(0, 0)).y()
                - self.calendar.height()
            )
        self.calendar.move(popup_x, popup_y)
        self.calendar.show()
        self.calendar.raise_()

    def _calendar_date_selected(self, date: QDate) -> None:
        self.setDateTime(QDateTime(date, self.dateTime().time()))
        if self.calendar is not None:
            self.calendar.hide()


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
        if parent is not None:
            parent_style = parent.styleSheet()
            if parent_style:
                self.setStyleSheet(parent_style)
        colors = getattr(
            parent,
            "colors",
            (
                DARK_COLORS
                if self.palette().color(self.backgroundRole()).lightness() < 128
                else COLORS
            ),
        )
        self.theme_colors = colors
        self.album_schedule_style = f"""
            QDateTimeEdit {{
                background-color: {colors['field']};
                color: {colors['ink']};
                border: 1px solid {colors['line']};
                border-radius: 8px;
                padding: 6px 36px 6px 10px;
            }}
        """
        self.calendar_style = f"""
            QCalendarWidget {{
                background-color: {colors['surface']};
                color: {colors['ink']};
                border: 1px solid {colors['line']};
                border-radius: 10px;
            }}
            QCalendarWidget QToolButton {{
                background-color: {colors['field']};
                color: {colors['ink']};
                border: 1px solid {colors['line']};
                border-radius: 6px;
                padding: 4px 6px;
                min-height: 28px;
                font-weight: 600;
            }}
            QCalendarWidget QToolButton#qt_calendar_prevmonth,
            QCalendarWidget QToolButton#qt_calendar_nextmonth {{
                min-width: 32px;
                max-width: 32px;
            }}
            QCalendarWidget QToolButton#qt_calendar_monthbutton {{
                min-width: 100px;
            }}
            QCalendarWidget QToolButton#qt_calendar_yearbutton {{
                min-width: 72px;
            }}
            QCalendarWidget QTableView {{
                background-color: {colors['surface']};
                color: {colors['ink']};
                selection-background-color: {colors['accent']};
                selection-color: #ffffff;
                border: 1px solid {colors['line']};
                alternate-background-color: {colors['field']};
            }}
            QCalendarWidget QTableView::item {{
                background-color: {colors['surface']};
                color: {colors['ink']};
                padding: 5px;
            }}
            QCalendarWidget QTableView::item:selected {{
                background-color: {colors['accent']};
                color: #ffffff;
            }}
            QWidget#qt_calendar_navigationbar {{
                background-color: {colors['field']};
                border: 0;
            }}
            QToolButton#calendarToggleButton {{
                background-color: {colors['field']};
                border: 1px solid {colors['line']};
                border-radius: 5px;
                padding: 2px;
            }}
            QToolButton#calendarToggleButton:hover {{
                background-color: {colors['hover']};
                border-color: {colors['accent']};
            }}
        """
        screen = self.screen()
        available = screen.availableGeometry() if screen else self.geometry()
        self.resize(
            min(available.width(), max(520, min(680, round(available.width() * 0.68)))),
            min(
                available.height(), max(360, min(500, round(available.height() * 0.72)))
            ),
        )
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Scheduling submits the post directly to Meta. Once Meta accepts it, "
            "the post publishes even when AutoPost Studio is closed. For an album, "
            "select 2–10 rows and set its shared date and time below. An internet "
            "connection is required to submit; Meta accepts times 10 minutes to "
            "30 days ahead."
        )
        hint.setObjectName("mutedText")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Image",
                "Caption",
                "Schedule (local)",
                "Destination",
                "Page ID",
                "Status",
                "ID",
            ]
        )
        self.table.setColumnHidden(6, True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        album_schedule_row = QHBoxLayout()
        self.album_schedule_label = QLabel("Shared album date/time (local)")
        self.album_schedule = CalendarDateTimeEdit()
        self._configure_calendar_picker(self.album_schedule)
        self.album_schedule.setDisplayFormat("yyyy-MM-dd HH:mm")
        album_now = QDateTime.currentDateTime()
        self.album_schedule.setMinimumDateTime(album_now)
        self.album_schedule.setDateTime(album_now.addSecs(15 * 60))
        album_schedule_row.addWidget(self.album_schedule_label)
        album_schedule_row.addWidget(self.album_schedule)
        album_schedule_row.addStretch(1)
        layout.addLayout(album_schedule_row)
        self.post_mode.currentIndexChanged.connect(
            self._update_album_schedule_visibility
        )
        self._update_album_schedule_visibility()
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 8, 0, 0)
        footer_layout.setSpacing(8)
        utility_row = QHBoxLayout()
        save_button = QPushButton("Save")
        manage_schedules_button = QPushButton("Meta schedules")
        delete_button = QPushButton("Delete")
        save_button.setToolTip("Save changes to the selected scheduler items.")
        manage_schedules_button.setToolTip(
            "Open Meta Business Suite to manage accepted schedules."
        )
        delete_button.setToolTip(
            "Delete selected scheduler items and their local files."
        )
        delete_button.setObjectName("dangerButton")
        save_button.clicked.connect(lambda: self._save_changes())
        manage_schedules_button.clicked.connect(self._open_meta_business_suite)
        delete_button.clicked.connect(self._delete_selected)
        utility_row.addWidget(save_button)
        utility_row.addStretch(1)
        utility_row.addWidget(manage_schedules_button)
        utility_row.addWidget(delete_button)
        footer_layout.addLayout(utility_row)

        action_row = QHBoxLayout()
        close_button = QPushButton("Close")
        post_button = QPushButton("Publish now")
        schedule_button = QPushButton("Schedule")
        post_button.setToolTip("Publish the selected images immediately.")
        schedule_button.setToolTip(
            "Submit the selected images to Facebook for scheduled publishing."
        )
        schedule_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.reject)
        post_button.clicked.connect(self._post_selected)
        schedule_button.clicked.connect(self._submit_schedule)
        for button in (
            save_button,
            manage_schedules_button,
            delete_button,
            close_button,
            post_button,
            schedule_button,
        ):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setMinimumHeight(36)
        action_row.addWidget(close_button)
        action_row.addStretch(1)
        action_row.addWidget(post_button)
        action_row.addWidget(schedule_button)
        footer_layout.addLayout(action_row)
        self.publish_action_buttons = [
            save_button,
            post_button,
            schedule_button,
            delete_button,
        ]
        self.delete_button = delete_button
        self.close_button = close_button
        self._publish_worker: PublishWorker | None = None
        layout.addWidget(footer)

    def _update_album_schedule_visibility(self, *_: Any) -> None:
        visible = self.post_mode.currentData() == "album"
        self.album_schedule_label.setVisible(visible)
        self.album_schedule.setVisible(visible)

    def _configure_calendar_picker(self, date_edit: QDateTimeEdit) -> None:
        colors = self.theme_colors
        date_edit.setStyleSheet(self.album_schedule_style)
        if not isinstance(date_edit, CalendarDateTimeEdit):
            raise TypeError("Calendar picker requires CalendarDateTimeEdit.")
        calendar = MonthOnlyCalendar()
        calendar.setParent(
            date_edit,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        date_edit.set_month_calendar(calendar)
        calendar.setStyle(QStyleFactory.create("Fusion"))
        calendar_palette = QPalette(calendar.palette())
        for role, color in (
            (QPalette.ColorRole.Window, colors["surface"]),
            (QPalette.ColorRole.Base, colors["surface"]),
            (QPalette.ColorRole.AlternateBase, colors["field"]),
            (QPalette.ColorRole.WindowText, colors["ink"]),
            (QPalette.ColorRole.Text, colors["ink"]),
            (QPalette.ColorRole.Button, colors["field"]),
            (QPalette.ColorRole.ButtonText, colors["ink"]),
            (QPalette.ColorRole.Highlight, colors["accent"]),
            (QPalette.ColorRole.HighlightedText, "#ffffff"),
        ):
            calendar_palette.setColor(role, QColor(color))
        calendar.setPalette(calendar_palette)
        calendar.setAutoFillBackground(True)
        calendar.setStyleSheet(self.calendar_style)
        icon_size = 18
        icon_pixmap = QPixmap(icon_size, icon_size)
        icon_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon_color = QColor(colors["ink"])
        painter.setPen(
            QPen(icon_color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRect(2, 4, 14, 12), 2, 2)
        painter.drawLine(2, 8, 16, 8)
        painter.drawLine(6, 2, 6, 6)
        painter.drawLine(12, 2, 12, 6)
        painter.drawEllipse(QPoint(6, 11), 1, 1)
        painter.drawEllipse(QPoint(10, 11), 1, 1)
        painter.drawEllipse(QPoint(6, 14), 1, 1)
        painter.drawEllipse(QPoint(10, 14), 1, 1)
        painter.end()
        date_edit.calendar_button.setIcon(QIcon(icon_pixmap))
        date_edit.calendar_button.setIconSize(QSize(icon_size, icon_size))
        date_edit.calendar_button.setStyleSheet(f"""
            QToolButton#calendarToggleButton {{
                background-color: {colors['field']};
                border: 1px solid {colors['line']};
                border-radius: 5px;
                padding: 2px;
            }}
            QToolButton#calendarToggleButton:hover {{
                background-color: {colors['hover']};
                border-color: {colors['accent']};
            }}
            """)
        calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        calendar.setHorizontalHeaderFormat(
            QCalendarWidget.HorizontalHeaderFormat.ShortDayNames
        )
        calendar.setGridVisible(True)
        calendar.setMinimumSize(340, 260)
        today = QDate.currentDate()
        calendar.setCurrentPage(today.year(), today.month())
        for child in calendar.findChildren(QWidget):
            child.setPalette(calendar_palette)
        for weekday in range(1, 8):
            weekday_format = QTextCharFormat()
            weekday_format.setForeground(QColor(colors["ink"]))
            calendar.setWeekdayTextFormat(Qt.DayOfWeek(weekday), weekday_format)

    def _open_meta_business_suite(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl("https://business.facebook.com/")):
            QMessageBox.warning(
                self,
                "Could not open Meta",
                "Open https://business.facebook.com/ in a browser to manage or cancel scheduled posts.",
            )

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
                if col in {0, 1, 3, 4, 5, 6}:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(record.image if col == 0 else value)
                self.table.setItem(row, col, item)
            status_details = [
                value for value in (record.last_error, record.remote_id) if value
            ]
            if record.status == "scheduled":
                status_details.insert(
                    0,
                    "Meta accepted this schedule. AutoPost Studio does not track "
                    "when Meta later publishes it; check Meta Business Suite.",
                )
            self.table.item(row, 5).setToolTip("\n".join(status_details))
            schedule = CalendarDateTimeEdit()
            self._configure_calendar_picker(schedule)
            schedule.setDisplayFormat("yyyy-MM-dd HH:mm")
            schedule_minimum = QDateTime(QDate.currentDate(), QTime(0, 0))
            scheduled_at: datetime | None = None
            if record.scheduled_at:
                try:
                    scheduled_at = datetime.strptime(
                        record.scheduled_at, "%Y-%m-%d %H:%M"
                    )
                    saved_schedule = QDateTime(
                        QDate(
                            scheduled_at.year,
                            scheduled_at.month,
                            scheduled_at.day,
                        ),
                        QTime(scheduled_at.hour, scheduled_at.minute),
                    )
                    if saved_schedule < schedule_minimum:
                        schedule_minimum = saved_schedule
                except ValueError:
                    schedule.setToolTip(
                        f"Invalid saved date/time: {record.scheduled_at}. Choose a valid date."
                    )
                    saved_schedule = None
            else:
                saved_schedule = None
            schedule.setMinimumDateTime(schedule_minimum)
            schedule.setSpecialValueText("Not scheduled")
            schedule.setDateTime(
                saved_schedule if saved_schedule is not None else schedule_minimum
            )
            schedule.setEnabled(record.status not in {"scheduled", "published"})
            self.table.setCellWidget(row, 2, schedule)
            if record.last_error:
                self.table.item(row, 5).setToolTip(record.last_error)

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
                    scheduled_at = datetime.strptime(scheduled_text, "%Y-%m-%d %H:%M")
                    if (
                        record.status in {"draft", "queued"}
                        and scheduled_at <= datetime.now()
                    ):
                        raise ValueError(
                            f"Choose a future date and time for {Path(record.image).name}."
                        )
                destination = self.table.item(row, 3).text().strip()
                page_id = self.table.item(row, 4).text().strip()
                updates.append((record, caption, scheduled_text, destination, page_id))
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
        album = self.post_mode.currentData() == "album"
        statuses_that_can_be_submitted = {
            "draft",
            "queued",
            "paused",
            "processing",
            "failed",
        }
        if any(
            record.status not in statuses_that_can_be_submitted for record in records
        ):
            QMessageBox.warning(
                self,
                "Post already submitted",
                "Only drafts, legacy local-queue items, or failed submissions can be sent to Meta. "
                "Manage already scheduled posts in Meta Business Suite.",
            )
            return
        if any(record.status == "processing" for record in records):
            answer = QMessageBox.question(
                self,
                "Check Meta before retrying",
                "A selected post was processing in the retired local scheduler. "
                "It may already have reached Facebook. Check Meta Business Suite for "
                "an existing scheduled or published post before submitting again. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if album:
            if not 2 <= len(records) <= 10:
                QMessageBox.warning(
                    self,
                    "Select 2–10 images",
                    "A scheduled album requires 2 to 10 images.",
                )
                return
            schedule_text = self.album_schedule.dateTime().toString("yyyy-MM-dd HH:mm")
            try:
                schedule_at = datetime.strptime(schedule_text, "%Y-%m-%d %H:%M")
            except ValueError:
                QMessageBox.warning(
                    self, "Invalid schedule time", "Choose a valid album date and time."
                )
                return
            try:
                _validate_meta_schedule(schedule_at)
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    "Invalid schedule time",
                    str(exc),
                )
                return
            rows_by_id = {record.id: row for row, record in enumerate(self.records)}
            for record in records:
                schedule = self.table.cellWidget(rows_by_id[record.id], 2)
                if not isinstance(schedule, QDateTimeEdit):
                    QMessageBox.critical(
                        self,
                        "Could not set album date",
                        "A schedule date/time control is missing.",
                    )
                    return
                schedule.setDateTime(self.album_schedule.dateTime())
        if not self._save_changes(show_message=False):
            return
        if album:
            if (
                len({record.page_id for record in records}) != 1
                or not records[0].page_id
            ):
                QMessageBox.warning(
                    self,
                    "One Page required",
                    "All album images must have the same Page ID.",
                )
                return
        if not album:
            missing_time = next(
                (record for record in records if not record.scheduled_at), None
            )
            if missing_time is not None:
                QMessageBox.warning(
                    self,
                    "Schedule time required",
                    f"Choose a date and time for {Path(missing_time.image).name}.",
                )
                return
        if album:
            self._publish_album(records, scheduled=True)
        else:
            self._publish_individual(records, scheduled=True)

    def _delete_selected(self) -> None:
        records = self._selected_records()
        if not records:
            QMessageBox.warning(
                self, "Select posts", "Select one or more scheduler rows to delete."
            )
            return
        answer = QMessageBox.question(
            self,
            "Delete selected posts?",
            f"Delete {len(records)} selected scheduler item(s), their sidecar files, "
            "and associated images? This cannot be undone."
            + (
                "\n\nThis only removes local files. It does not cancel a post already "
                "scheduled with Meta; cancel that post in Meta Business Suite first."
                if any(record.status == "scheduled" for record in records)
                else ""
            ),
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
        self.publish_status.setText(
            f"Deleted {len(records)} scheduler item(s) and associated files."
        )
        self.publish_status.show()

    def _selected_records(self) -> list[PostRecord]:
        rows = sorted(
            {index.row() for index in self.table.selectionModel().selectedRows()}
        )
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
                self,
                "Facebook setup required",
                "Add a Page access token in Options → Facebook Pages.",
            )
            return None
        return token

    def _facebook_tokens(
        self, records: list[PostRecord]
    ) -> str | dict[str, str] | None:
        parent = self.parent()
        if isinstance(parent, AutoPostStudio):
            return parent._facebook_tokens_for_records(records)
        return self._facebook_token()

    def _publish_individual(self, records: list[PostRecord], scheduled: bool) -> None:
        schedule_times: list[datetime | None] = []
        if scheduled:
            try:
                for record in records:
                    if not record.scheduled_at:
                        raise ValueError(
                            f"Enter a schedule time for {Path(record.image).name}."
                        )
                    schedule_at = datetime.strptime(
                        record.scheduled_at, "%Y-%m-%d %H:%M"
                    )
                    _validate_meta_schedule(schedule_at)
                    schedule_times.append(schedule_at)
            except ValueError as exc:
                QMessageBox.warning(self, "Schedule time required", str(exc))
                return
        else:
            schedule_times = [None] * len(records)
        if any(not record.page_id for record in records):
            QMessageBox.warning(
                self,
                "Page ID required",
                "Enter a Facebook Page ID for every selected image.",
            )
            return
        tokens = self._facebook_tokens(records)
        if not tokens:
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
            records, tokens, scheduled, schedule_times, album=False
        )

    def _publish_album(
        self, records: list[PostRecord], scheduled: bool = False
    ) -> None:
        if not 2 <= len(records) <= 10:
            QMessageBox.warning(
                self,
                "Select 2–10 images",
                "A Facebook album post requires 2 to 10 selected images.",
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
                    "Set one shared future date and time for every image in the album.",
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
            try:
                _validate_meta_schedule(schedule_at)
            except ValueError as exc:
                QMessageBox.warning(self, "Invalid schedule time", str(exc))
                return
        tokens = self._facebook_tokens(records)
        if not tokens:
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
            tokens,
            scheduled,
            [schedule_at] * len(records),
            album=True,
        )

    def _start_publish_worker(
        self,
        records: list[PostRecord],
        tokens: str | dict[str, str],
        scheduled: bool,
        schedule_times: list[datetime | None],
        album: bool,
    ) -> None:
        if scheduled:
            try:
                for record in records:
                    record.status = "draft"
                    record.group_id = ""
                    record.last_error = ""
                    save_post(record, self.folder)
            except OSError as exc:
                QMessageBox.critical(
                    self,
                    "Could not prepare Meta schedule",
                    str(exc),
                )
                try:
                    self.records = load_posts(self.folder)
                    self._populate()
                except (OSError, ValueError):
                    self.log.exception(
                        "Could not refresh records after failing to prepare Meta schedule"
                    )
                return
            self._populate()

        self._set_publish_busy(True, scheduled=scheduled)
        self.publish_status.setStyleSheet("")
        self.publish_progress.setStyleSheet("")
        self.publish_status.setText(
            "Submitting album to Facebook for scheduling…"
            if album and scheduled
            else (
                "Uploading album to Facebook…"
                if album
                else f"Starting Facebook {'schedule' if scheduled else 'publishing'}…"
            )
        )
        self.publish_status.show()
        self.publish_progress.setRange(0, 0)
        self.publish_progress.show()
        worker = PublishWorker(records, tokens, scheduled, schedule_times, album)
        worker.progress.connect(self._publish_progress)
        worker.finished.connect(self._publish_thread_finished)
        worker.completed.connect(
            lambda results, failure: self._publish_completed(
                records, results, failure, scheduled, album
            )
        )
        self._publish_worker = worker
        worker.start()

    def _set_publish_busy(self, busy: bool, scheduled: bool = False) -> None:
        for button in self.publish_action_buttons:
            button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        if busy:
            self.setWindowTitle(
                "Post scheduler · Submitting to Meta…"
                if scheduled
                else "Post scheduler · Publishing…"
            )
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
            group_id = uuid.uuid4().hex
            for record in records:
                record.status = status
                record.remote_id = post_id
                record.group_id = group_id
                try:
                    save_post(record, self.folder)
                except OSError as exc:
                    local_errors.append(f"{Path(record.image).name}: {exc}")
            completed = len(records)
        elif not album:
            for index, result in results:
                record = records[index]
                record.status = status
                record.remote_id = str(result.get("post_id") or result.get("id") or "")
                record.group_id = ""
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
            message = (
                f"Failed or incomplete: {completed} of {total} completed. "
                + " ".join(details)
            )
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
    """Embedded shell with protected history and asynchronous command output."""

    def __init__(
        self,
        submit_command: Any,
        parent: QWidget | None = None,
        prompt_provider: Any | None = None,
        interrupt_command: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.submit_command = submit_command
        self.prompt_provider = prompt_provider or (lambda: "autopost")
        self.interrupt_command = interrupt_command
        self.command_history: list[str] = []
        self.history_index = 0
        self.input_start = 0
        self.command_pending = False
        self.setObjectName("terminalOutput")
        self.setUndoRedoEnabled(False)
        self.appendPlainText(
            f"{APP_NAME} {APP_VERSION} · PowerShell and AutoPost automation console\n"
            "Type `help` for commands. Use Ctrl+C to stop a running command."
        )
        self._write_prompt()

    def _end_cursor(self) -> Any:
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        return cursor

    def _write_prompt(self) -> None:
        cursor = self._end_cursor()
        if self.toPlainText() and not self.toPlainText().endswith("\n"):
            cursor.insertText("\n")
        cursor.insertText(f"{self.prompt_provider()}> ")
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
            cursor.insertText(result.rstrip("\n"))
        self.input_start = cursor.position()
        self.setTextCursor(cursor)
        self._write_prompt()

    def append_command_output(self, text: str) -> None:
        if not text:
            return
        cursor = self._end_cursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def finish_command(self, exit_code: int) -> None:
        if not self.command_pending:
            return
        self.command_pending = False
        if exit_code:
            self.append_command_output(f"\n[process exited with code {exit_code}]\n")
        elif self.document().characterCount() > 1:
            cursor = self._end_cursor()
            if cursor.position() and self.toPlainText()[-1:] != "\n":
                cursor.insertText("\n")
        self._write_prompt()

    def _replace_current_command(self, value: str) -> None:
        cursor = self._end_cursor()
        cursor.setPosition(self.input_start)
        cursor.movePosition(cursor.MoveOperation.End, cursor.MoveMode.KeepAnchor)
        cursor.insertText(value)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event: Any) -> None:
        key = event.key()
        modifiers = event.modifiers()
        if self.command_pending:
            if (
                modifiers & Qt.KeyboardModifier.ControlModifier
                and key == Qt.Key.Key_C
                and self.interrupt_command is not None
            ):
                self.interrupt_command()
            return
        cursor = self.textCursor()
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            command = self._current_command().strip()
            if command:
                self.command_history.append(command)
            self.history_index = len(self.command_history)
            cursor = self._end_cursor()
            cursor.insertText("\n")
            self.setTextCursor(cursor)
            if command.casefold() in {"clear", "cls"}:
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
            if result is None:
                self.command_pending = True
            else:
                self._show_result(result)
            return
        if key == Qt.Key.Key_Up:
            if self.command_history:
                self.history_index = max(0, self.history_index - 1)
                self._replace_current_command(self.command_history[self.history_index])
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
        if self.command_pending:
            return
        cursor = self.textCursor()
        if (
            cursor.hasSelection()
            and min(cursor.anchor(), cursor.position()) >= self.input_start
        ):
            super().cut()
            return
        self.setTextCursor(self._end_cursor())

    def insertFromMimeData(self, source: Any) -> None:
        if self.command_pending:
            return
        cursor = self.textCursor()
        if cursor.position() < self.input_start or (
            cursor.hasSelection()
            and min(cursor.anchor(), cursor.position()) < self.input_start
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
        self.working_directory = Path.cwd().resolve()
        self.previous_directory: Path | None = None
        self._command_active = False
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Windows shell · commands run in the current folder"))
        layout.addLayout(toolbar)
        self.terminal = TerminalConsole(
            self._execute_command,
            self,
            prompt_provider=self._prompt,
            interrupt_command=self._interrupt_command,
        )
        layout.addWidget(self.terminal, 1)
        self.terminal.setFocus()

    def _prompt(self) -> str:
        return f"PS {self.working_directory}"

    @staticmethod
    def _split_arguments(text: str) -> list[str]:
        try:
            args = shlex.split(text, posix=False)
        except ValueError as exc:
            raise ValueError(f"Could not parse command: {exc}") from exc
        return [
            (
                value[1:-1]
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
                else value
            )
            for value in args
        ]

    def _execute_command(self, text: str) -> str | None:
        try:
            args = self._split_arguments(text)
        except ValueError:
            return self._start_shell_command(text)
        if not args:
            return ""
        command = args[0].casefold()
        if command == "autopost":
            args = args[1:]
            if not args:
                return self._help_text()
            command = args[0].casefold()
        if command in {"exit", "quit"}:
            self.accept()
            return ""
        if command == "cd":
            return self._change_directory(args[1:])
        if command in {"pwd", "get-location"} and len(args) == 1:
            return str(self.working_directory)
        if command == "help" and len(args) == 1:
            return self._help_text()
        if command in {"status", "frame", "publish", "queue"}:
            return self._run_automation(args)
        if command in {"open-outputs", "outputs"} and len(args) == 1:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(OUTPUT_DIR)))
            return f"Opened {OUTPUT_DIR}"
        return self._start_shell_command(text)

    def _change_directory(self, args: list[str]) -> str:
        if len(args) > 1:
            return "Usage: cd [path]"
        try:
            if not args:
                target = Path.home()
            elif args[0] == "-":
                if self.previous_directory is None:
                    return "No previous directory is available."
                target = self.previous_directory
            else:
                value = os.path.expandvars(args[0])
                target = Path(value).expanduser()
                if not target.is_absolute():
                    target = self.working_directory / target
            target = target.resolve(strict=True)
            if not target.is_dir():
                return f"Not a directory: {target}"
        except (OSError, RuntimeError) as exc:
            return f"Could not change directory: {exc}"
        self.previous_directory, self.working_directory = (
            self.working_directory,
            target,
        )
        return ""

    @staticmethod
    def _help_text() -> str:
        return (
            "Windows PowerShell console\n"
            "Commands run through Windows PowerShell in the current directory.\n"
            "  cd [path]             Change the console working directory\n"
            "  cd -                  Return to the previous directory\n"
            "  pwd                   Show the current directory\n"
            "  cls                   Clear the console\n"
            "  help                  Show this help\n"
            "  exit                  Close the console\n"
            "  frame, status, queue, publish  Run AutoPost automation commands\n"
            "  Any other command is run by PowerShell in the current directory.\n"
            "  Ctrl+C interrupts a running shell process.\n"
            "Examples:\n"
            '  cd "C:\\Users\\you\\Pictures\\Campaign"\n'
            "  frame .\\frame.png .\\incoming --output .\\exports --recursive\n"
            "  Get-ChildItem"
        )

    def _run_automation(self, args: list[str]) -> str:
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from .automation_cli import main as automation_cli_main

        stdout = StringIO()
        stderr = StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                parent = self.parent()
                default_page_id = (
                    parent.active_facebook_page_id
                    if isinstance(parent, AutoPostStudio)
                    and args[0].casefold() == "publish"
                    else ""
                )
                exit_code = automation_cli_main(
                    args,
                    cwd=self.working_directory,
                    default_page_id=default_page_id,
                )
        except SystemExit as exc:
            exit_code = int(exc.code) if isinstance(exc.code, int) else 0
        result = stdout.getvalue().strip()
        errors = stderr.getvalue().strip()
        if errors:
            result = f"{result}\n{errors}".strip()
        if exit_code and not result:
            result = f"Command exited with status {exit_code}."
        return result

    def _start_shell_command(self, text: str) -> str | None:
        if self._command_active:
            return "A command is already running."
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if powershell:
            program = powershell
            arguments = [
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::OutputEncoding = "
                "[System.Text.UTF8Encoding]::new($false); " + text,
            ]
        else:
            command_processor = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
            if not command_processor:
                return "No PowerShell or Command Prompt executable was found."
            program = command_processor
            arguments = ["/d", "/s", "/c", text]
        self.process.setWorkingDirectory(str(self.working_directory))
        self._command_active = True
        self.process.start(program, arguments)
        return None

    def _read_stdout(self) -> None:
        self.terminal.append_command_output(
            bytes(self.process.readAllStandardOutput()).decode(
                "utf-8", errors="replace"
            )
        )

    def _read_stderr(self) -> None:
        self.terminal.append_command_output(
            bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        )

    def _process_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        self._read_stdout()
        self._read_stderr()
        self._command_active = False
        if exit_status == QProcess.ExitStatus.CrashExit and exit_code == 0:
            exit_code = 1
        self.terminal.finish_command(exit_code)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart:
            return
        self._command_active = False
        self.terminal.append_command_output(
            f"Could not start shell command: {self.process.errorString()}\n"
        )
        self.terminal.finish_command(1)

    def _interrupt_command(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.process.terminate()
        QTimer.singleShot(
            1500,
            lambda: (
                self.process.kill()
                if self.process.state() != QProcess.ProcessState.NotRunning
                else None
            ),
        )

    def closeEvent(self, event: Any) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(1000):
                self.process.kill()
                self.process.waitForFinished(1000)
        super().closeEvent(event)


class AutoPostStudio(QMainWindow):
    """Main composition workspace and entry point to post-management tools."""

    def __init__(self) -> None:
        super().__init__()
        configure_logging()
        self.log = logging.getLogger("frame_studio")
        self.preferences = load_preferences()
        self.facebook_pages = _configured_facebook_pages(self.preferences)
        configured_page_ids = {page["page_id"] for page in self.facebook_pages}
        preferred_page_id = str(
            self.preferences.get(
                "active_facebook_page_id",
                self.preferences.get("facebook_page_id", ""),
            )
        )
        self.active_facebook_page_id = (
            preferred_page_id
            if preferred_page_id in configured_page_ids
            else self.facebook_pages[0]["page_id"] if self.facebook_pages else ""
        )
        self.dark_mode = bool(self.preferences.get("dark_mode", False))
        self.colors = dict(DARK_COLORS if self.dark_mode else COLORS)
        default_frame = DEFAULT_FRAME_PATH
        default_photo = APP_INSTALL_DIR / "image.jpg"
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
        self._zoom_generation = 0
        self._workers: set[QThread] = set()
        self._batch: BatchWorker | None = None
        self._batch_frame: Path | None = None
        self._gallery_worker: GalleryWorker | None = None
        self.selected_photos: list[Path] = []
        self.single_photo_before_batch = ""
        self.use_frame = bool(self.preferences.get("use_frame", True))
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
        central.setObjectName("appSurface")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(30, 22, 30, 26)
        root.setSpacing(16)

        top = QHBoxLayout()
        top.setContentsMargins(2, 0, 2, 0)
        brand = QVBoxLayout()
        brand.setSpacing(3)
        title = QLabel("AutoPost Studio")
        title.setObjectName("appTitle")
        subtitle = QLabel("Create, prepare, and publish your content")
        subtitle.setObjectName("mutedText")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        top.addLayout(brand)
        top.addStretch(1)
        self.mode_badge = QLabel("CREATOR WORKSPACE")
        self.mode_badge.setObjectName("badge")
        top.addWidget(self.mode_badge, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        page_selection = QHBoxLayout()
        page_selection.setContentsMargins(14, 8, 14, 8)
        page_selection.setSpacing(12)
        page_selection_frame = QFrame()
        page_selection_frame.setObjectName("pageBar")
        page_selection_frame.setLayout(page_selection)
        current_page_label = QLabel("CURRENT FACEBOOK PAGE")
        current_page_label.setObjectName("fieldLabel")
        page_selection.addWidget(current_page_label)
        self.current_page_combo = QComboBox()
        self.current_page_combo.setMinimumWidth(250)
        self.current_page_combo.setMinimumHeight(38)
        self.current_page_combo.currentIndexChanged.connect(
            self._active_facebook_page_changed
        )
        self._refresh_page_selector()
        page_selection.addWidget(self.current_page_combo)
        page_selection.addStretch(1)
        root.addWidget(page_selection_frame)

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
        left_layout.setContentsMargins(24, 24, 24, 24)
        left_layout.setSpacing(13)
        left_layout.addWidget(self._heading("Create a post"))
        left_layout.addWidget(
            self._muted("Choose your frame, image, and export location.")
        )
        left_layout.addSpacing(4)
        self.frame_browse_button = self._picker(
            left_layout, "FRAME OVERLAY", self.frame_path, self._browse_frame
        )
        self.use_frame_checkbox = QCheckBox("Use frame overlay")
        self.use_frame_checkbox.setChecked(self.use_frame)
        self.use_frame_checkbox.toggled.connect(self._frame_usage_changed)
        left_layout.addWidget(self.use_frame_checkbox)
        self.frame_path.setEnabled(self.use_frame)
        self.frame_browse_button.setEnabled(self.use_frame)
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
        right_layout.setContentsMargins(22, 22, 22, 22)
        right_layout.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(self._heading("Live preview"))
        header.addStretch(1)
        self.preview_info = QLabel("Facebook-ready preview · 3:2 fit")
        self.preview_info.setObjectName("mutedText")
        header.addWidget(self.preview_info)
        self.inspect_preview_button = QPushButton("Inspect full size")
        self.inspect_preview_button.setEnabled(False)
        self.inspect_preview_button.clicked.connect(self._inspect_single_preview)
        header.addWidget(self.inspect_preview_button)
        right_layout.addLayout(header)
        self.preview_stack = QStackedWidget()
        self.preview = PreviewCanvas()
        self.preview.inspectRequested.connect(self._inspect_single_preview)
        self.preview.setMinimumSize(120, 100)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.preview_stack.addWidget(self.preview)
        self.gallery = PreviewGallery()
        self.gallery.zoomRequested.connect(self._zoom_selected_photo)
        self.gallery.selectionChanged.connect(self._gallery_selection_changed)
        self.preview_stack.addWidget(self.gallery)
        right_layout.addWidget(self.preview_stack, 1)
        activity_header = QHBoxLayout()
        activity_header.addWidget(self._heading("Recent exports"))
        activity_header.addStretch(1)
        self.clear_activity_button = QToolButton()
        self.clear_activity_button.setObjectName("clearActivityButton")
        self.clear_activity_button.setToolTip("Clear recent export history")
        self.clear_activity_button.setAccessibleName("Clear recent export history")
        self.clear_activity_button.setCursor(Qt.CursorShape.PointingHandCursor)
        trash_icon = QPixmap(18, 18)
        trash_icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(trash_icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self.colors["danger"]), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(5, 4, 13, 4)
        painter.drawLine(7, 2, 11, 2)
        painter.drawRoundedRect(QRect(6, 5, 7, 11), 1, 1)
        painter.drawLine(8, 7, 8, 14)
        painter.drawLine(11, 7, 11, 14)
        painter.end()
        self.clear_activity_button.setIcon(QIcon(trash_icon))
        self.clear_activity_button.setIconSize(QSize(18, 18))
        self.clear_activity_button.clicked.connect(self._clear_recent_activity)
        activity_header.addWidget(self.clear_activity_button)
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
            "Import photos from Google Drive…",
            self._import_google_drive,
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
        self._action(options_menu, "Google Drive…", self._google_drive_settings)
        self._action(options_menu, "Facebook Pages…", self._api_settings)
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
                self, "AutoPost Studio", f"Version {APP_VERSION}\nYou are up to date!"
            ),
        )

    def _refresh_page_selector(self) -> None:
        self.current_page_combo.blockSignals(True)
        self.current_page_combo.clear()
        if not self.facebook_pages:
            self.current_page_combo.addItem("No Facebook Pages configured", "")
            self.current_page_combo.setEnabled(False)
        else:
            self.current_page_combo.setEnabled(True)
            for page in self.facebook_pages:
                label = f"{page['name']} · {page['page_id']}"
                self.current_page_combo.addItem(label, page["page_id"])
                self.current_page_combo.setItemData(
                    self.current_page_combo.count() - 1,
                    label,
                    Qt.ItemDataRole.ToolTipRole,
                )
            selected_index = self.current_page_combo.findData(
                self.active_facebook_page_id
            )
            self.current_page_combo.setCurrentIndex(max(0, selected_index))
        self.current_page_combo.blockSignals(False)

    def _active_facebook_page_changed(self, index: int) -> None:
        if index < 0:
            return
        page_id = str(self.current_page_combo.currentData() or "")
        if page_id == self.active_facebook_page_id:
            return
        self.active_facebook_page_id = page_id
        self.preferences["active_facebook_page_id"] = page_id
        self.preferences["facebook_page_id"] = page_id
        self._save_preferences()

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
        layout.addWidget(self._heading("Buy me a coffee"))
        support_message = QLabel(
            "Support your developer with any amount to help improve the quality "
            "of future projects. Support is entirely optional. GCash only."
        )
        support_message.setWordWrap(True)
        layout.addWidget(support_message)
        support_button = QPushButton("Buy me a coffee")
        support_button.clicked.connect(self._show_gcash_qr)
        layout.addWidget(support_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_gcash_qr(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Support via GCash")
        layout = QVBoxLayout(dialog)
        instructions = QLabel(
            "Scan this QR code with GCash to send any amount. This is optional "
            "support for the developer. GCash is the only accepted payment method."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        if GCASH_QR_PATH.suffix.lower() == ".svg":
            renderer = QSvgRenderer(str(GCASH_QR_PATH))
            if not renderer.isValid():
                QMessageBox.critical(
                    self,
                    "GCash QR image unavailable",
                    f"Could not load the configured QR image:\n{GCASH_QR_PATH}",
                )
                return
            qr_pixmap = QPixmap(320, 320)
            qr_pixmap.fill(Qt.GlobalColor.white)
            painter = QPainter(qr_pixmap)
            renderer.render(painter)
            painter.end()
        else:
            qr_pixmap = QPixmap(str(GCASH_QR_PATH))
            if qr_pixmap.isNull():
                QMessageBox.critical(
                    self,
                    "GCash QR image unavailable",
                    f"Could not load the configured QR image:\n{GCASH_QR_PATH}",
                )
                return

        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setPixmap(qr_pixmap)
        layout.addWidget(image)
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
            QWidget {{ color: {c['ink']}; font-family: 'Segoe UI'; font-size: 10pt; }}
            QMainWindow, QDialog {{ background-color: {c['background']}; }}
            QMenuBar {{ background-color: {c['background']}; border-bottom: 1px solid {c['line']}; padding: 4px 10px; }}
            QMenuBar::item {{ padding: 7px 11px; border-radius: 6px; }}
            QMenuBar::item:selected, QMenu::item:selected {{ background-color: {c['hover']}; }}
            QMenu {{ background-color: {c['surface']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 5px; }}
            QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 5px; }}
            #appSurface {{ background-color: {c['background']}; }}
            #appTitle {{ color: {c['ink']}; font-size: 24pt; font-weight: 700; }}
            #heading {{ color: {c['ink']}; font-size: 12pt; font-weight: 650; }}
            #mutedText, #statusText {{ color: {c['muted']}; }}
            #fieldLabel {{ color: {c['muted']}; font-size: 8pt; font-weight: 700; }}
            #badge {{ color: {c['accent']}; background-color: {c['field']}; border: 1px solid {c['line']}; border-radius: 12px; padding: 7px 12px; font-size: 8pt; font-weight: 700; }}
            #pageBar {{ background-color: {c['surface']}; border: 1px solid {c['line']}; border-radius: 10px; }}
            #surfaceCard {{ background-color: {c['surface']}; border: 1px solid {c['line']}; border-radius: 14px; }}
            QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{ background-color: {c['field']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 8px 10px; selection-background-color: {c['accent']}; }}
            QLineEdit, QSpinBox, QComboBox {{ min-height: 20px; }}
            QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {c['muted']}; }}
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
            QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {c['muted']}; background-color: {c['background']}; }}
            QComboBox::drop-down {{ border: 0; width: 26px; }}
            QPushButton {{ background-color: {c['surface']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 9px 14px; min-height: 18px; font-weight: 600; }}
            QPushButton:hover {{ background-color: {c['hover']}; border-color: {c['accent']}; }}
            QPushButton:pressed {{ background-color: {c['stage']}; }}
            QPushButton:disabled {{ color: {c['muted']}; background-color: {c['field']}; border-color: {c['line']}; }}
            #primaryButton {{ background-color: {c['accent']}; color: #ffffff; border-color: {c['accent']}; min-height: 22px; }}
            #primaryButton:hover {{ background-color: {c['accent_hover']}; color: #ffffff; }}
            #primaryButton:disabled {{ color: #c9cbd4; background-color: {c['muted']}; }}
            #dangerButton {{ color: {c['danger']}; border-color: {c['line']}; }}
            #dangerButton:hover {{ background-color: {c['danger']}; color: #ffffff; border-color: {c['danger']}; }}
            QToolButton#clearActivityButton {{ background-color: {c['surface']}; border: 1px solid {c['line']}; border-radius: 8px; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; }}
            QToolButton#clearActivityButton:hover {{ background-color: {c['field']}; border-color: {c['danger']}; }}
            QToolButton#clearActivityButton:disabled {{ background-color: {c['field']}; }}
            QCheckBox {{ spacing: 9px; }}
            QScrollArea {{ background-color: {c['background']}; border: 0; }}
            QScrollArea QWidget#qt_scrollarea_viewport {{ background-color: {c['background']}; }}
            #previewCanvas {{ background-color: {c['stage']}; border: 1px solid {c['line']}; border-radius: 10px; color: {c['muted']}; padding: 12px; }}
            #galleryTile {{ background-color: {c['field']}; border: 1px solid {c['line']}; border-radius: 9px; }}
            #galleryImage {{ background-color: {c['stage']}; border-radius: 6px; color: {c['muted']}; font-size: 8pt; }}
            #galleryName {{ color: {c['muted']}; font-size: 8pt; }}
            #galleryRemaining {{ color: {c['accent']}; font-weight: 650; }}
            QListWidget {{ background-color: {c['surface']}; border: 1px solid {c['line']}; border-radius: 9px; padding: 5px; }}
            QListWidget::item {{ padding: 7px; border-radius: 6px; }}
            QListWidget::item:hover {{ background-color: {c['hover']}; }}
            QListWidget::item:selected {{ color: {c['ink']}; background-color: {c['stage']}; border: 1px solid {c['line']}; }}
            QProgressBar {{ background-color: {c['stage']}; border: 0; border-radius: 3px; }}
            QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: 3px; }}
            QTableWidget {{ background-color: {c['surface']}; alternate-background-color: {c['field']}; border: 1px solid {c['line']}; border-radius: 9px; gridline-color: {c['line']}; selection-background-color: {c['stage']}; selection-color: {c['ink']}; }}
            QTableWidget::item {{ padding: 5px; }}
            QHeaderView::section {{ background-color: {c['field']}; color: {c['muted']}; border: 0; padding: 9px 8px; font-size: 8pt; font-weight: 700; }}
            QScrollBar:vertical {{ background-color: transparent; width: 12px; margin: 3px; }}
            QScrollBar::handle:vertical {{ background-color: {c['line']}; border-radius: 5px; min-height: 28px; }}
            QScrollBar::handle:vertical:hover {{ background-color: {c['muted']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; border: 0; }}
            QToolTip {{ color: {c['ink']}; background-color: {c['surface']}; border: 1px solid {c['line']}; padding: 5px 7px; }}
            QSplitter::handle {{ background-color: transparent; }}
            #terminalOutput {{ background-color: #080808; color: #d8f5df; border: 1px solid {c['line']}; border-radius: 9px; padding: 14px; font-family: Consolas, monospace; font-size: 10pt; selection-background-color: #334433; }}
        """)

    def _browse_frame(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a transparent frame",
            self.frame_path.text(),
            "PNG images (*.png);;" + IMAGE_FILTER,
        )
        if path:
            self.frame_path.setText(path)
            self._save_preferences()

    def _frame_usage_changed(self, enabled: bool) -> None:
        self.use_frame = enabled
        self.frame_path.setEnabled(enabled)
        self.frame_browse_button.setEnabled(enabled)
        self._zoom_generation += 1
        self._save_preferences()
        self._schedule_preview()

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
        self._zoom_generation += 1
        self._preview_generation += 1
        if not self.selected_photos:
            self.inspect_preview_button.setEnabled(False)
        self._timer.start()

    def _start_preview(self) -> None:
        frame = Path(self.frame_path.text()).expanduser() if self.use_frame else None
        if self.selected_photos:
            if frame is not None and not frame.is_file():
                self.gallery.show_loading(self.selected_photos)
                self.preview_info.setText("Load a frame to preview the selected images")
                return
            generation = self._preview_generation
            worker = GalleryWorker(generation, frame, self.selected_photos, self.fit)
            worker.ready.connect(self._gallery_ready)
            worker.failed.connect(self._gallery_failed)
            self._gallery_worker = worker
            self._run_worker(worker)
            return
        photo = Path(self.photo_path.text()).expanduser()
        if (frame is not None and not frame.is_file()) or not photo.is_file():
            message = (
                "Load a frame and an image to preview your post"
                if self.use_frame
                else "Load an image to preview your post"
            )
            self.preview.clear_image(message)
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

    def _preview_ready(self, generation: int, image: Image.Image, size: object) -> None:
        if generation != self._preview_generation:
            return
        self.preview.set_image(image)
        self.inspect_preview_button.setEnabled(True)
        width, height = size
        self.preview_info.setText(
            f"{'Framed composition' if self.use_frame else 'Original photo'}"
            f" · {width} × {height} px"
        )
        self.status.setText("Preview is ready")

    def _preview_failed(self, generation: int, error: str) -> None:
        if generation != self._preview_generation:
            return
        self.inspect_preview_button.setEnabled(False)
        self.preview.clear_image("Could not load the selected image or frame")
        self.status.setText(error)
        self.log.error("Preview failed: %s", error)

    def _gallery_ready(
        self, generation: int, previews: list[tuple[str, QImage | None]]
    ) -> None:
        if generation != self._preview_generation or not self.selected_photos:
            return
        self.gallery.set_previews(previews)
        self.gallery.set_selection_info(
            self.gallery.items.currentRow(), len(self.selected_photos)
        )
        self.preview_stack.setCurrentWidget(self.gallery)
        self.preview_info.setText(f"Batch preview · {len(self.selected_photos)} photos")
        self.status.setText(f"{len(self.selected_photos)} images ready to export")

    def _gallery_selection_changed(self, index: int) -> None:
        if not hasattr(self, "selected_photos"):
            return
        total = len(self.selected_photos)
        self.gallery.set_selection_info(index, total)
        if 0 <= index < total:
            self.preview_info.setText(
                f"Previewing {index + 1} of {total} · "
                f"{self.selected_photos[index].name}"
            )

    def _zoom_selected_photo(self, index: int) -> None:
        if not 0 <= index < len(self.selected_photos):
            return
        photo = self.selected_photos[index]
        self._start_zoom_preview(photo)

    def _inspect_single_preview(self) -> None:
        if self.selected_photos:
            index = self.gallery.items.currentRow()
            self._zoom_selected_photo(index)
            return
        photo = Path(self.photo_path.text()).expanduser()
        if photo.is_file():
            self._start_zoom_preview(photo)

    def _start_zoom_preview(self, photo: Path) -> None:
        frame = Path(self.frame_path.text()).expanduser() if self.use_frame else None
        if frame is not None and not frame.is_file():
            QMessageBox.warning(
                self,
                "Frame required",
                "Load a valid frame before zooming this preview.",
            )
            return
        self._zoom_generation += 1
        generation = self._zoom_generation
        worker = PreviewWorker(generation, frame, photo, self.fit)
        worker.ready.connect(
            lambda result_generation, image, size, source=photo: (
                self._zoom_preview_ready(result_generation, source, image, size)
            )
        )
        worker.failed.connect(
            lambda result_generation, error, source=photo: (
                self._zoom_preview_failed(result_generation, source, error)
            )
        )
        self._run_worker(worker)

    def _zoom_preview_ready(
        self,
        generation: int,
        photo: Path,
        image: Image.Image,
        size: tuple[int, int],
    ) -> None:
        if generation != self._zoom_generation:
            return
        title = f"{photo.name} · {size[0]} × {size[1]} px"
        PreviewZoomDialog(title, image, self).exec()

    def _zoom_preview_failed(self, generation: int, photo: Path, error: str) -> None:
        if generation != self._zoom_generation:
            return
        self.log.error("Could not generate zoom preview for %s: %s", photo, error)
        QMessageBox.warning(
            self,
            "Preview unavailable",
            f"Could not create a preview for {photo.name}:\n{error}",
        )

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
        frame = Path(self.frame_path.text()).expanduser() if self.use_frame else None
        photo = Path(self.photo_path.text()).expanduser()
        if (frame is not None and not frame.is_file()) or not photo.is_file():
            message = (
                "Select a valid frame and image."
                if self.use_frame
                else "Select a valid image."
            )
            QMessageBox.warning(self, "Images required", message)
            return
        output_dir = Path(self.output_dir.text()).expanduser()
        output_suffix = "framed" if self.use_frame else "post"
        output = output_dir / (
            f"{photo.stem}_{output_suffix}.{self.output_format.currentData()}"
        )
        self._set_busy(True, 0, 0)
        self.save_button.setEnabled(False)
        self.save_button.setText("Exporting…")
        QApplication.processEvents()
        try:
            source = load_photo(photo)
            composed = (
                attach_frame(load_frame(frame), source, self.fit)
                if frame is not None
                else source
            )
            save_image(composed, output, self.quality.value())
            save_post(
                PostRecord(
                    image=str(output.resolve()),
                    caption=self.caption.toPlainText().strip(),
                    page_id=self.active_facebook_page_id,
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
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose images", "", IMAGE_FILTER)
        if paths:
            self._set_selected_photos([Path(path) for path in paths])

    def _load_image_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose image folder")
        if not folder:
            return
        try:
            photos = sorted(
                p
                for p in Path(folder).iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
            )
        except OSError as exc:
            QMessageBox.critical(self, "Could not read folder", str(exc))
            return
        if not photos:
            QMessageBox.information(
                self,
                "No images found",
                "No supported images were found in this folder.",
            )
            return
        self._set_selected_photos(photos)

    def _set_selected_photos(self, photos: list[Path]) -> None:
        if not self.selected_photos:
            self.single_photo_before_batch = self.photo_path.text()
        self.selected_photos = photos
        self.photo_path.clear()
        self.photo_path.setPlaceholderText(
            "Unavailable while multiple images are selected"
        )
        self.photo_path.setEnabled(False)
        self.source_browse_button.setEnabled(False)
        self.inspect_preview_button.setVisible(False)
        self.preview_stack.setCurrentWidget(self.gallery)
        self.gallery.show_loading(photos)
        self._zoom_generation += 1
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
        self._zoom_generation += 1
        self.inspect_preview_button.setVisible(True)
        self.inspect_preview_button.setEnabled(False)
        self.save_button.setText("Export image")
        self.preview_stack.setCurrentWidget(self.preview)
        self._schedule_preview()

    def _start_batch(self, photos: list[Path]) -> None:
        frame = Path(self.frame_path.text()).expanduser() if self.use_frame else None
        if frame is not None and not frame.is_file():
            QMessageBox.warning(
                self, "Frame required", "Load a frame before exporting images."
            )
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
            self.active_facebook_page_id,
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
        frame = self._batch_frame
        for photo, output in outputs:
            self._record_export(photo, frame, output)
        self._batch_frame = None
        self.status.setText(f"Batch finished · {succeeded} of {total} images exported")
        if succeeded != total:
            QMessageBox.warning(
                self,
                "Batch completed with errors",
                f"{succeeded} of {total} images exported. See the log for skipped images.",
            )
        else:
            QMessageBox.information(
                self, "Batch complete", f"Exported {succeeded} images."
            )
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

    def _record_export(self, photo: Path, frame: Path | None, output: Path) -> None:
        try:
            record_activity(
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "photo": str(photo),
                    "frame": str(frame) if frame is not None else "",
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

    def _clear_recent_activity(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear recent exports",
            "Clear the recent export list and delete its history file?\n\n"
            f"{ACTIVITY_PATH}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            try:
                ACTIVITY_PATH.unlink()
            except FileNotFoundError:
                pass
        except OSError as exc:
            self.log.exception("Could not clear recent export history")
            QMessageBox.critical(
                self,
                "Could not clear recent exports",
                f"The export history file could not be deleted:\n{exc}",
            )
            return
        self.activity.setRowCount(0)

    def _show_scheduler(self) -> None:
        folder = self._scheduler_directory()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            SchedulerDialog(
                folder,
                self,
                self.active_facebook_page_id,
            ).exec()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Scheduler unavailable", str(exc))

    def _facebook_tokens_for_records(
        self, records: list[PostRecord]
    ) -> str | dict[str, str] | None:
        tokens: dict[str, str] = {}
        legacy_page_id = str(self.preferences.get("facebook_page_id", ""))
        try:
            for page_id in {record.page_id for record in records}:
                token = get_secret(_facebook_page_secret_name(page_id))
                if not token and page_id == legacy_page_id:
                    token = get_secret("facebook_page_token")
                if not token:
                    QMessageBox.warning(
                        self,
                        "Facebook Page setup required",
                        f"No access token is saved for Page {page_id}. "
                        "Add the Page and its token in Options → Facebook Pages.",
                    )
                    return None
                tokens[page_id] = token
        except RuntimeError as exc:
            QMessageBox.critical(self, "Credential vault unavailable", str(exc))
            return None
        if len(tokens) == 1:
            return next(iter(tokens.values()))
        return tokens

    def _scheduler_directory(self) -> Path:
        return Path(
            str(
                self.preferences.get("scheduler_dir")
                or self.output_dir.text()
                or OUTPUT_DIR
            )
        ).expanduser()

    def _google_drive_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Google Drive")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        instructions = QLabel(
            "Choose the OAuth client JSON file for a Google Cloud Desktop app. "
            "The app requests read-only access to Drive and stores its refresh "
            "authorization in the operating-system credential vault."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        setup_link = QLabel(
            '<a href="https://console.cloud.google.com/apis/credentials">'
            "Google Cloud API credentials</a> · "
            '<a href="https://console.cloud.google.com/apis/library/drive.googleapis.com">'
            "Enable Google Drive API</a>"
        )
        setup_link.setOpenExternalLinks(True)
        layout.addWidget(setup_link)

        client_secrets = QLineEdit(
            str(self.preferences.get("google_drive_client_secrets", ""))
        )
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(
            lambda: self._browse_google_drive_credentials(client_secrets)
        )
        credential_row = QHBoxLayout()
        credential_row.addWidget(client_secrets, 1)
        credential_row.addWidget(browse_button)
        layout.addLayout(credential_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(
            lambda: self._save_google_drive_credentials(dialog, client_secrets)
        )
        layout.addWidget(buttons)
        disconnect_button = QPushButton("Disconnect Google Drive")
        disconnect_button.clicked.connect(
            lambda: self._disconnect_google_drive(dialog)
        )
        layout.addWidget(disconnect_button)
        dialog.exec()

    def _browse_google_drive_credentials(self, field: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Google OAuth Desktop client JSON",
            field.text(),
            "JSON files (*.json)",
        )
        if path:
            field.setText(path)

    def _save_google_drive_credentials(
        self, dialog: QDialog, field: QLineEdit
    ) -> None:
        path = Path(field.text()).expanduser()
        try:
            validate_client_secrets(path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Invalid Google OAuth client", str(exc))
            return
        self.preferences["google_drive_client_secrets"] = str(path.resolve())
        self._save_preferences()
        dialog.accept()

    def _disconnect_google_drive(self, dialog: QDialog) -> None:
        answer = QMessageBox.question(
            dialog,
            "Disconnect Google Drive",
            "Remove the saved Google Drive authorization from the credential vault?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_secret(DRIVE_TOKEN_SECRET)
        except RuntimeError as exc:
            QMessageBox.critical(dialog, "Credential vault unavailable", str(exc))
            return
        self.preferences.pop("google_drive_client_secrets", None)
        self._save_preferences()
        dialog.accept()

    def _import_google_drive(self) -> None:
        client_secrets = Path(
            str(self.preferences.get("google_drive_client_secrets", ""))
        ).expanduser()
        if not client_secrets.is_file():
            QMessageBox.information(
                self,
                "Google Drive setup",
                "Choose a Google OAuth Desktop client JSON file in "
                "Options → Google Drive before importing photos.",
            )
            return
        dialog = DriveImportDialog(
            self,
            client_secrets,
            PREFERENCES_PATH.parent / "google_drive_imports",
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.imported_paths:
            self._set_selected_photos(dialog.imported_paths)

    def _choose_scheduler_folder(self) -> None:
        current = str(
            self.preferences.get("scheduler_dir")
            or self.output_dir.text()
            or OUTPUT_DIR
        )
        folder = QFileDialog.getExistingDirectory(
            self, "Choose scheduler folder", current
        )
        if folder:
            self.preferences["scheduler_dir"] = folder
            self._save_preferences()
            QMessageBox.information(
                self, "Scheduler folder", f"Scheduler now reads:\n{folder}"
            )

    def _reset(self) -> None:
        self._clear_batch_selection()
        self.frame_path.clear()
        self.photo_path.clear()
        self.caption.clear()
        self.preview.clear_image("Load a frame and an image to preview your post")
        self.preview_stack.setCurrentWidget(self.preview)
        self.preview_info.setText(
            "Facebook-ready preview · 3:2 fit"
            if self.use_frame
            else "Original photo · no frame"
        )
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
        dialog.setWindowTitle("Facebook Pages")
        dialog.setMinimumWidth(620)
        layout = QVBoxLayout(dialog)
        pages_list = QListWidget()
        pages_list.setMinimumHeight(130)
        layout.addWidget(pages_list)
        form = QFormLayout()
        page_name = QLineEdit()
        page_name.setPlaceholderText("A name to recognize this Page")
        page_id = QLineEdit()
        page_id.setPlaceholderText("Your Facebook Page ID")
        page_token = QLineEdit()
        page_token.setEchoMode(QLineEdit.EchoMode.Password)
        page_token.setPlaceholderText(
            "System User token recommended; stored in your credential vault"
        )
        form.addRow("Page name", page_name)
        form.addRow("Facebook Page ID", page_id)
        form.addRow("System User / Page token", page_token)
        layout.addLayout(form)

        note = self._muted(
            "For a business-owned Page, create a System User in Meta Business Settings, "
            "assign the app and Page with content-creation access, then generate a token "
            "with the required Pages permissions. Never request publish_actions. "
            "Each token is stored in the operating system credential vault."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        connection_status = QLabel("")
        connection_status.setWordWrap(True)
        layout.addWidget(connection_status)

        page_values: list[dict[str, str]] = []
        try:
            legacy_page_id = str(self.preferences.get("facebook_page_id", ""))
            for page in self.facebook_pages:
                token = get_secret(_facebook_page_secret_name(page["page_id"]))
                if not token and page["page_id"] == legacy_page_id:
                    token = get_secret("facebook_page_token")
                page_values.append({**page, "token": token})
        except RuntimeError as exc:
            QMessageBox.critical(self, "Credential vault unavailable", str(exc))
            return

        def test_page_access() -> None:
            if not page_id.text().strip() or not page_token.text().strip():
                connection_status.setText(
                    "Enter both the Page ID and its access token before testing."
                )
                connection_status.setStyleSheet(f"color: {self.colors['danger']};")
                return
            test_button.setEnabled(False)
            test_button.setText("Testing…")
            self._set_busy(True)
            try:
                name = validate_page_access(
                    page_id.text().strip(), page_token.text().strip()
                )
            except (FacebookError, ValueError) as exc:
                connection_status.setText(f"Connection failed: {exc}")
                connection_status.setStyleSheet(f"color: {self.colors['danger']};")
            else:
                page_name.setText(name)
                connection_status.setText(
                    f"Connected to Page: {name}. This confirms Page access, "
                    "but does not test publish permissions."
                )
                connection_status.setStyleSheet(f"color: {self.colors['success']};")
            finally:
                test_button.setEnabled(True)
                test_button.setText("Test Page access")
                self._set_busy(False)

        def show_page(index: int) -> None:
            if not 0 <= index < len(page_values):
                page_name.clear()
                page_id.clear()
                page_token.clear()
                return
            page = page_values[index]
            page_name.setText(page["name"])
            page_id.setText(page["page_id"])
            page_token.setText(page["token"])
            connection_status.clear()

        def refresh_page_list(selected: int = -1) -> None:
            pages_list.clear()
            for page in page_values:
                pages_list.addItem(f"{page['name']} · {page['page_id']}")
            if page_values:
                pages_list.setCurrentRow(min(max(0, selected), len(page_values) - 1))
            else:
                show_page(-1)

        def save_page() -> bool:
            name = page_name.text().strip()
            identifier = page_id.text().strip()
            token = page_token.text().strip()
            if not name or not identifier:
                QMessageBox.warning(
                    dialog, "Page details required", "Enter a Page name and Page ID."
                )
                return False
            existing_index = next(
                (
                    index
                    for index, page in enumerate(page_values)
                    if page["page_id"] == identifier
                ),
                -1,
            )
            if not token and existing_index >= 0:
                token = page_values[existing_index]["token"]
            if not token:
                QMessageBox.warning(
                    dialog,
                    "Page token required",
                    "Enter an access token for this Page.",
                )
                return False
            value = {"name": name, "page_id": identifier, "token": token}
            if existing_index >= 0:
                page_values[existing_index] = value
                selected_index = existing_index
            else:
                page_values.append(value)
                selected_index = len(page_values) - 1
            refresh_page_list(selected_index)
            return True

        def remove_page() -> None:
            selected_index = pages_list.currentRow()
            if not 0 <= selected_index < len(page_values):
                return
            page_values.pop(selected_index)
            refresh_page_list(selected_index)

        pages_list.currentRowChanged.connect(show_page)
        test_button = QPushButton("Test Page access")
        test_button.clicked.connect(test_page_access)
        editor_buttons = QHBoxLayout()
        editor_buttons.addWidget(test_button)
        editor_buttons.addStretch(1)
        add_button = QPushButton("Add / update Page")
        add_button.clicked.connect(save_page)
        editor_buttons.addWidget(add_button)
        remove_button = QPushButton("Remove selected")
        remove_button.clicked.connect(remove_page)
        editor_buttons.addWidget(remove_button)
        layout.addLayout(editor_buttons)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        refresh_page_list(
            next(
                (
                    index
                    for index, page in enumerate(page_values)
                    if page["page_id"] == self.active_facebook_page_id
                ),
                0,
            )
        )

        def save_settings() -> None:
            if (
                page_name.text().strip()
                or page_id.text().strip()
                or page_token.text().strip()
            ) and not save_page():
                return
            try:
                for page in page_values:
                    if not page["token"]:
                        raise ValueError(
                            f"Enter an access token for Page {page['name']}."
                        )
                    set_secret(
                        _facebook_page_secret_name(page["page_id"]), page["token"]
                    )
            except (RuntimeError, ValueError) as exc:
                QMessageBox.critical(
                    dialog, "Could not save Page credentials", str(exc)
                )
                return

            active_page_ids = {page["page_id"] for page in page_values}
            active_page_id = (
                self.active_facebook_page_id
                if self.active_facebook_page_id in active_page_ids
                else page_values[0]["page_id"] if page_values else ""
            )
            updated_preferences = dict(self.preferences)
            updated_preferences.update(
                {
                    "facebook_pages": [
                        {"name": page["name"], "page_id": page["page_id"]}
                        for page in page_values
                    ],
                    "active_facebook_page_id": active_page_id,
                    "facebook_page_id": active_page_id,
                }
            )
            try:
                save_json(PREFERENCES_PATH, updated_preferences)
            except OSError as exc:
                QMessageBox.critical(dialog, "Could not save Page settings", str(exc))
                return

            retained_page_ids = active_page_ids
            removed_page_ids = {
                page["page_id"] for page in self.facebook_pages
            } - retained_page_ids
            self.preferences = updated_preferences
            self.facebook_pages = [
                {"name": page["name"], "page_id": page["page_id"]}
                for page in page_values
            ]
            self.active_facebook_page_id = active_page_id
            self._refresh_page_selector()
            for removed_page_id in removed_page_ids:
                try:
                    delete_secret(_facebook_page_secret_name(removed_page_id))
                except RuntimeError as exc:
                    QMessageBox.warning(
                        dialog,
                        "Page removed with credential cleanup issue",
                        f"The Page was removed, but its saved token could not be "
                        f"deleted from the credential vault: {exc}",
                    )
            dialog.accept()

        buttons.accepted.connect(save_settings)
        dialog.exec()

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
        model_values = (
            {
                provider_id: str(
                    saved_models.get(
                        provider_id,
                        (
                            self.preferences.get("caption_model", config["model"])
                            if provider_id == selected_provider
                            else config["model"]
                        ),
                    )
                )
                for provider_id, config in PROVIDER_DEFAULTS.items()
            }
            if isinstance(saved_models, dict)
            else {
                provider_id: str(config["model"])
                for provider_id, config in PROVIDER_DEFAULTS.items()
            }
        )
        endpoint_values = (
            {
                provider_id: str(
                    saved_endpoints.get(
                        provider_id,
                        (
                            self.preferences.get("caption_endpoint", config["endpoint"])
                            if provider_id == selected_provider
                            else config["endpoint"]
                        ),
                    )
                )
                for provider_id, config in PROVIDER_DEFAULTS.items()
            }
            if isinstance(saved_endpoints, dict)
            else {
                provider_id: str(config["endpoint"])
                for provider_id, config in PROVIDER_DEFAULTS.items()
            }
        )
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
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
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
                "caption": (
                    self.caption.toPlainText() if hasattr(self, "caption") else ""
                ),
                "dark_mode": self.dark_mode,
                "use_frame": self.use_frame,
                "facebook_pages": self.facebook_pages,
                "active_facebook_page_id": self.active_facebook_page_id,
                "facebook_page_id": self.active_facebook_page_id,
            }
        )
        try:
            save_json(PREFERENCES_PATH, self.preferences)
        except OSError:
            self.log.exception("Could not save preferences")


class DriveImportDialog(QDialog):
    """Browse Drive folders and download a selected set of photos."""

    def __init__(
        self,
        owner: AutoPostStudio,
        client_secrets_path: Path,
        download_dir: Path,
    ) -> None:
        super().__init__(owner)
        self.owner = owner
        self.client_secrets_path = client_secrets_path
        self.download_dir = download_dir
        self.folder_stack: list[DriveFile] = []
        self.imported_paths: list[Path] = []
        self.setWindowTitle("Import photos from Google Drive")
        self.resize(600, 520)

        layout = QVBoxLayout(self)
        title = QLabel("Choose photos from Google Drive")
        title.setObjectName("heading")
        layout.addWidget(title)
        description = QLabel(
            "Sign in when your browser opens, browse folders, then check the "
            "photos to import into the current batch."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        navigation = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._go_back)
        navigation.addWidget(self.back_button)
        self.folder_label = QLabel("My Drive")
        self.folder_label.setObjectName("mutedText")
        navigation.addWidget(self.folder_label, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._load_folder)
        navigation.addWidget(self.refresh_button)
        layout.addLayout(navigation)

        self.items = QListWidget()
        self.items.itemDoubleClicked.connect(self._activate_item)
        layout.addWidget(self.items, 1)
        self.status_label = QLabel("Connecting to Google Drive…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.import_button = buttons.addButton(
            "Import selected", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.import_button.clicked.connect(self._download_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load_folder()

    def _current_folder_id(self) -> str:
        return self.folder_stack[-1].file_id if self.folder_stack else "root"

    def _update_navigation(self) -> None:
        self.back_button.setEnabled(bool(self.folder_stack))
        self.folder_label.setText(
            "My Drive"
            if not self.folder_stack
            else "My Drive / " + " / ".join(
                folder.name for folder in self.folder_stack
            )
        )

    def _set_busy(self, busy: bool, status: str) -> None:
        self.status_label.setText(status)
        self.progress.setVisible(busy)
        self.items.setEnabled(not busy)
        self.back_button.setEnabled(not busy and bool(self.folder_stack))
        self.refresh_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy)

    def _load_folder(self) -> None:
        self._update_navigation()
        self.items.clear()
        self.items.addItem("Loading Google Drive…")
        self._set_busy(True, "Connecting to Google Drive…")
        worker = DriveListingWorker(
            self.client_secrets_path,
            self._current_folder_id(),
            self.owner,
        )
        worker.ready.connect(self._folder_loaded)
        worker.failed.connect(self._folder_failed)
        self.owner._run_worker(worker)

    def _folder_loaded(self, entries: list[DriveFile]) -> None:
        self.items.clear()
        folders = [entry for entry in entries if entry.is_folder]
        images = [entry for entry in entries if not entry.is_folder]
        for entry in [*folders, *images]:
            item = QListWidgetItem(
                f"Folder  |  {entry.name}" if entry.is_folder else entry.name
            )
            item.setData(Qt.ItemDataRole.UserRole, entry)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not entry.is_folder:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
                item.setCheckState(Qt.CheckState.Unchecked)
            item.setFlags(flags)
            self.items.addItem(item)
        if not entries:
            self.items.addItem("No folders or supported photos in this folder.")
        self._set_busy(False, f"{len(folders)} folders · {len(images)} photos")
        self.import_button.setEnabled(bool(images))

    def _folder_failed(self, error: str) -> None:
        self.items.clear()
        self._set_busy(False, "Could not load this Google Drive folder.")
        self.import_button.setEnabled(False)
        QMessageBox.critical(self, "Google Drive error", error)

    def _activate_item(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, DriveFile) and entry.is_folder:
            self.folder_stack.append(entry)
            self._load_folder()

    def _go_back(self) -> None:
        if self.folder_stack:
            self.folder_stack.pop()
            self._load_folder()

    def _download_selected(self) -> None:
        selected = [
            entry
            for index in range(self.items.count())
            if (entry := self.items.item(index).data(Qt.ItemDataRole.UserRole))
            and isinstance(entry, DriveFile)
            and not entry.is_folder
            and self.items.item(index).checkState() == Qt.CheckState.Checked
        ]
        if not selected:
            QMessageBox.information(
                self, "No photos selected", "Check one or more photos to import."
            )
            return
        self._set_busy(True, f"Downloading {len(selected)} selected photo(s)…")
        worker = DriveDownloadWorker(
            self.client_secrets_path,
            selected,
            self.download_dir,
            self.owner,
        )
        worker.ready.connect(self._downloads_ready)
        worker.failed.connect(self._downloads_failed)
        self.owner._run_worker(worker)

    def _downloads_ready(self, paths: list[Path]) -> None:
        if not paths:
            self._downloads_failed("No photos were downloaded.")
            return
        self.imported_paths = paths
        self.accept()

    def _downloads_failed(self, error: str) -> None:
        self._set_busy(False, "Photo import failed.")
        QMessageBox.critical(self, "Google Drive download failed", error)


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = AutoPostStudio()
    window.showMaximized()
    app.exec()
