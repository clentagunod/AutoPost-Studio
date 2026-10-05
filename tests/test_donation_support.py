"""Tests for the optional GCash support section in developer information."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from src.app import AutoPostStudio
from src.config import GCASH_QR_PATH


class DonationSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_buy_me_a_coffee_opens_configured_gcash_qr(self) -> None:
        with (
            patch("src.app.configure_logging"),
            patch("src.app.load_preferences", return_value={"use_frame": False}),
            patch("src.app.load_activity", return_value=[]),
            patch("src.app.save_json"),
        ):
            window = AutoPostStudio()
        window._timer.stop()
        opened_dialogs: list[QDialog] = []

        def record_dialog(dialog: QDialog) -> QDialog.DialogCode:
            opened_dialogs.append(dialog)
            return QDialog.DialogCode.Rejected

        try:
            with patch.object(QDialog, "exec", record_dialog):
                window._show_developer_info()
                developer_dialog = opened_dialogs[-1]
                copy = " ".join(
                    label.text()
                    for label in developer_dialog.findChildren(QLabel)
                )
                self.assertIn("any amount", copy)
                self.assertIn("entirely optional", copy)
                self.assertIn("GCash only", copy)

                coffee_button = next(
                    button
                    for button in developer_dialog.findChildren(QPushButton)
                    if button.text() == "Buy me a coffee"
                )
                coffee_button.click()

            self.assertEqual(len(opened_dialogs), 2)
            qr_dialog = opened_dialogs[-1]
            self.assertIn("GCash is the only accepted payment method", " ".join(
                label.text() for label in qr_dialog.findChildren(QLabel)
            ))
            self.assertTrue(GCASH_QR_PATH.is_file())
            qr_images = []
            for label in qr_dialog.findChildren(QLabel):
                pixmap = label.pixmap()
                if pixmap is not None and not pixmap.isNull():
                    qr_images.append(pixmap)
            self.assertTrue(qr_images)
        finally:
            for dialog in opened_dialogs:
                dialog.close()
            window.close()


if __name__ == "__main__":
    unittest.main()
