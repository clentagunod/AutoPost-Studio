"""Tests for the embedded Windows shell and AutoPost command console."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from src.app import TerminalDialog


class TerminalDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_cd_persists_and_updates_prompt_for_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "project folder"
            target.mkdir()
            dialog = TerminalDialog(None)
            previous = dialog.working_directory

            result = dialog._execute_command(f'cd "{target}"')

            self.assertEqual(result, "")
            self.assertEqual(dialog.working_directory, target.resolve())
            self.assertIn(str(target.resolve()), dialog._prompt())
            self.assertEqual(dialog._execute_command("cd -"), "")
            self.assertEqual(dialog.working_directory, previous)
            dialog.close()

    def test_auto_post_frame_command_uses_console_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(folder / "frame.png")
            Image.new("RGBA", (10, 10), (40, 80, 120, 255)).save(
                folder / "photo.png"
            )
            dialog = TerminalDialog(None)
            dialog.working_directory = folder

            dialog._execute_command(
                "frame frame.png photo.png --output exports/result.png"
            )

            self.assertTrue((folder / "exports" / "result.png").is_file())
            dialog.close()

    @unittest.skipUnless(
        shutil.which("pwsh.exe") or shutil.which("powershell.exe"),
        "Windows PowerShell is not available",
    )
    def test_shell_commands_are_started_in_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dialog = TerminalDialog(None)
            dialog.working_directory = Path(temporary)
            with patch.object(dialog.process, "start") as start:
                result = dialog._execute_command(
                    'Write-Output "embedded shell works"'
                )

            self.assertIsNone(result)
            self.assertEqual(
                dialog.process.workingDirectory(),
                str(Path(temporary)),
            )
            start.assert_called_once()
            self.assertIn("Write-Output", start.call_args.args[1][-1])
            self.assertEqual(
                dialog.process.state(), QProcess.ProcessState.NotRunning
            )
            dialog.close()

    def test_help_documents_shell_and_auto_post_commands(self) -> None:
        dialog = TerminalDialog(None)

        result = dialog._execute_command("help")

        self.assertIn("Windows PowerShell", result)
        self.assertIn("cd [path]", result)
        self.assertIn("frame, status, queue, publish", result)
        dialog.close()


if __name__ == "__main__":
    unittest.main()
