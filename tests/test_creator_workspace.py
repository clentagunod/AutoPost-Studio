"""Tests for batch preview and frameless creator exports."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.app import (
    AutoPostStudio,
    BatchWorker,
    GALLERY_THUMBNAIL_SIZE,
    GalleryWorker,
    QMessageBox,
    PreviewGallery,
    PreviewZoomDialog,
)


class CreatorWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_gallery_worker_generates_thumbnails_for_every_photo_without_frame(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            photos = []
            for index in range(10):
                path = folder / f"photo-{index}.png"
                Image.new("RGBA", (900, 700), (index * 10, 80, 120, 255)).save(
                    path
                )
                photos.append(path)

            worker = GalleryWorker(3, None, photos, "cover")
            emitted: list[tuple[int, list[tuple[str, QImage | None]]]] = []
            worker.ready.connect(
                lambda generation, previews: emitted.append(
                    (generation, previews)
                )
            )
            failures: list[str] = []
            worker.failed.connect(lambda _generation, error: failures.append(error))

            worker.run()

            self.assertEqual(failures, [])
            self.assertEqual(len(emitted), 1)
            generation, previews = emitted[0]
            self.assertEqual(generation, 3)
            self.assertEqual(len(previews), 10)
            self.assertTrue(all(preview is not None for _, preview in previews))
            for _, preview in previews:
                assert preview is not None
                self.assertLessEqual(preview.width(), GALLERY_THUMBNAIL_SIZE[0])
                self.assertLessEqual(preview.height(), GALLERY_THUMBNAIL_SIZE[1])

    def test_frameless_batch_worker_exports_original_photos(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            Image.new("RGBA", (20, 12), (30, 120, 210, 255)).save(source)
            output_dir = root / "outputs"
            metadata_dir = root / "metadata"
            worker = BatchWorker(
                None,
                [source],
                output_dir,
                "cover",
                95,
                "png",
                "A caption",
                "page-1",
                metadata_dir,
            )
            completed: list[tuple[int, int, list[tuple[Path, Path]]]] = []
            worker.complete.connect(
                lambda succeeded, total, outputs: completed.append(
                    (succeeded, total, outputs)
                )
            )
            failures: list[str] = []
            worker.failed.connect(failures.append)

            worker.run()

            self.assertEqual(failures, [])
            self.assertEqual(len(completed), 1)
            succeeded, total, outputs = completed[0]
            self.assertEqual((succeeded, total), (1, 1))
            output = outputs[0][1]
            self.assertEqual(output.name, "source_001_post.png")
            with Image.open(output) as exported:
                self.assertEqual(exported.size, (20, 12))
                self.assertEqual(exported.getpixel((0, 0)), (30, 120, 210, 255))
            metadata_files = list(metadata_dir.glob("*.autopost"))
            self.assertEqual(len(metadata_files), 1)
            self.assertIn(output.stem, metadata_files[0].name)

    def test_framed_batch_worker_still_composites_the_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            frame = root / "frame.png"
            Image.new("RGBA", (20, 12), (30, 120, 210, 255)).save(source)
            frame_image = Image.new("RGBA", (20, 12), (0, 0, 0, 0))
            frame_image.putpixel((0, 0), (220, 10, 30, 255))
            frame_image.save(frame)

            worker = BatchWorker(
                frame,
                [source],
                root / "outputs",
                "cover",
                95,
                "png",
                "",
                "page-1",
                root / "metadata",
            )
            completed: list[tuple[int, int, list[tuple[Path, Path]]]] = []
            worker.complete.connect(
                lambda succeeded, total, outputs: completed.append(
                    (succeeded, total, outputs)
                )
            )

            worker.run()

            self.assertEqual(completed[0][:2], (1, 1))
            output = completed[0][2][0][1]
            self.assertEqual(output.name, "source_001_framed.png")
            with Image.open(output) as exported:
                self.assertEqual(exported.getpixel((0, 0)), (220, 10, 30, 255))
                self.assertEqual(exported.getpixel((1, 0)), (30, 120, 210, 255))

    def test_gallery_lists_every_preview_and_updates_selection(self) -> None:
        gallery = PreviewGallery()
        previews = [
            (f"photo-{index}.png", None)
            for index in range(10)
        ]

        gallery.show_loading([Path(name) for name, _ in previews])
        gallery.set_previews(previews)
        gallery.items.setCurrentRow(8)
        gallery.set_selection_info(gallery.items.currentRow(), len(previews))

        self.assertEqual(gallery.items.count(), 10)
        self.assertEqual(gallery.items.currentRow(), 8)
        self.assertEqual(gallery.selection_info.text(), "Photo 9 of 10")
        self.assertTrue(gallery.zoom_button.isEnabled())
        gallery.close()

    def test_zoom_dialog_scales_full_resolution_image(self) -> None:
        dialog = PreviewZoomDialog(
            "photo.png", Image.new("RGBA", (100, 80), "red"), None
        )
        initial_zoom = dialog._zoom_factor

        event = QWheelEvent(
            QPointF(30, 30),
            QPointF(30, 30),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        dialog.scroll_area.wheelEvent(event)

        self.assertGreater(dialog._zoom_factor, initial_zoom)
        self.assertEqual(
            dialog.zoom_value.text(),
            f"{round(dialog._zoom_factor * 100)}%",
        )
        dialog.close()

    def test_single_preview_can_be_opened_with_inspect_button(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            photo = Path(temporary) / "photo.png"
            Image.new("RGBA", (100, 80), "red").save(photo)
            with (
                patch("src.app.configure_logging"),
                patch(
                    "src.app.load_preferences",
                    return_value={"use_frame": False},
                ),
                patch("src.app.load_activity", return_value=[]),
                patch("src.app.save_json"),
            ):
                window = AutoPostStudio()
                window.photo_path.setText(str(photo))
                window._timer.stop()
                window._preview_ready(
                    window._preview_generation,
                    Image.new("RGBA", (100, 80), "red"),
                    (100, 80),
                )
                self.assertTrue(window.inspect_preview_button.isEnabled())
                with patch.object(window, "_start_zoom_preview") as inspect:
                    window.inspect_preview_button.click()

                inspect.assert_called_once_with(photo)
                window.close()

    def test_single_preview_canvas_double_click_requests_inspection(self) -> None:
        with (
            patch("src.app.configure_logging"),
            patch("src.app.load_preferences", return_value={}),
            patch("src.app.load_activity", return_value=[]),
            patch("src.app.save_json"),
        ):
            window = AutoPostStudio()
            window.preview.set_image(Image.new("RGBA", (100, 80), "red"))
            window.preview.show()
            inspected: list[bool] = []
            window.preview.inspectRequested.connect(
                lambda: inspected.append(True)
            )

            QTest.mouseDClick(
                window.preview,
                Qt.MouseButton.LeftButton,
                pos=window.preview.rect().center(),
            )

            self.assertEqual(inspected, [True])
            window._timer.stop()
            window.close()

    def test_plain_wheel_scroll_does_not_zoom(self) -> None:
        dialog = PreviewZoomDialog(
            "photo.png", Image.new("RGBA", (100, 80), "red")
        )
        initial_zoom = dialog._zoom_factor
        event = QWheelEvent(
            QPointF(30, 30),
            QPointF(30, 30),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

        dialog.scroll_area.wheelEvent(event)

        self.assertEqual(dialog._zoom_factor, initial_zoom)
        dialog.close()

    def test_frame_checkbox_controls_and_saves_frame_usage(self) -> None:
        with (
            patch("src.app.configure_logging"),
            patch("src.app.load_preferences", return_value={}),
            patch("src.app.load_activity", return_value=[]),
            patch("src.app.save_json"),
        ):
            window = AutoPostStudio()

            self.assertTrue(window.use_frame_checkbox.isChecked())
            self.assertTrue(window.frame_path.isEnabled())
            self.assertTrue(window.frame_browse_button.isEnabled())

            window.use_frame_checkbox.setChecked(False)

            self.assertFalse(window.frame_path.isEnabled())
            self.assertFalse(window.frame_browse_button.isEnabled())
            self.assertFalse(window.preferences["use_frame"])
            window._timer.stop()
            window.close()

    def test_clear_recent_exports_deletes_history_file_and_clears_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            history_file = Path(temporary) / "recent_activity.json"
            history_file.write_text('[{"output":"old.png"}]', encoding="utf-8")
            with (
                patch("src.app.configure_logging"),
                patch("src.app.load_preferences", return_value={}),
                patch("src.app.load_activity", return_value=[{"output": "old.png"}]),
                patch("src.app.save_json"),
                patch("src.app.ACTIVITY_PATH", history_file),
                patch(
                    "src.app.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
            ):
                window = AutoPostStudio()
                self.assertEqual(window.activity.rowCount(), 1)

                window.clear_activity_button.click()

                self.assertFalse(history_file.exists())
                self.assertEqual(window.activity.rowCount(), 0)
                window._timer.stop()
                window.close()


if __name__ == "__main__":
    unittest.main()
