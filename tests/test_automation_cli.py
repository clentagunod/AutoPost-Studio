"""Tests for the app automation CLI."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src.automation_cli import main
from src.cli import main as image_cli_main
from src.scheduler import load_posts


class AutomationCliTests(unittest.TestCase):
    def test_status_command_succeeds_with_empty_output_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "outputs"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("src.automation_cli.OUTPUT_DIR", output_dir),
                patch("src.automation_cli.load_preferences", return_value={}),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                result = main(["status"])

            self.assertEqual(result, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn(f"Outputs: {output_dir}", stdout.getvalue())
            self.assertIn("Posts: 0", stdout.getvalue())

    def test_frame_file_defaults_to_app_output_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            frame = folder / "frame.png"
            photo = folder / "portrait.jpg"
            frame.write_bytes(b"frame")
            photo.write_bytes(b"photo")
            output_dir = folder / "app-outputs"
            with (
                patch("src.cli.OUTPUT_DIR", output_dir),
                patch("src.cli.load_frame", return_value=object()),
                patch("src.cli.process_one", return_value=True) as process_one,
            ):
                result = image_cli_main([str(frame), str(photo)])

            self.assertEqual(result, 0)
            self.assertEqual(
                process_one.call_args.args[2],
                output_dir / "portrait_framed.png",
            )

    def test_frame_folder_defaults_to_app_output_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "source"
            source.mkdir()
            frame = folder / "frame.png"
            photo = source / "portrait.jpg"
            frame.write_bytes(b"frame")
            photo.write_bytes(b"photo")
            output_dir = folder / "app-outputs"
            with (
                patch("src.cli.OUTPUT_DIR", output_dir),
                patch("src.cli.load_frame", return_value=object()),
                patch("src.cli.process_one", return_value=True) as process_one,
            ):
                result = image_cli_main([str(frame), str(source)])

            self.assertEqual(result, 0)
            self.assertEqual(
                process_one.call_args.args[2],
                output_dir / "portrait_framed.png",
            )

    def test_queue_add_creates_local_post_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "image.png"
            image.write_bytes(b"test image")
            schedule = (datetime.now() + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M"
            )
            with patch(
                "src.automation_cli.load_preferences",
                return_value={"scheduler_dir": str(folder), "facebook_page_id": "page-1"},
            ):
                result = main(
                    [
                        "queue",
                        "add",
                        str(image),
                        "--caption",
                        "Test caption",
                        "--schedule",
                        schedule,
                        "--json",
                    ]
                )

            self.assertEqual(result, 0)
            record = load_posts(folder)[0]
            self.assertEqual(record.status, "queued")
            self.assertEqual(record.caption, "Test caption")
            self.assertEqual(record.page_id, "page-1")
            self.assertEqual(record.scheduled_at, schedule)

    def test_queue_list_can_emit_json_for_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "image.png"
            image.write_bytes(b"test")
            from src.scheduler import PostRecord, save_post

            record = PostRecord(image=str(image), status="queued")
            save_post(record, folder)
            stdout = io.StringIO()
            with (
                patch(
                    "src.automation_cli.load_preferences",
                    return_value={"scheduler_dir": str(folder)},
                ),
                contextlib.redirect_stdout(stdout),
            ):
                result = main(["queue", "list", "--status", "queued", "--json"])

            self.assertEqual(result, 0)
            output = json.loads(stdout.getvalue())
            self.assertEqual([item["id"] for item in output], [record.id])

    def test_queue_delete_requires_explicit_confirmation_flag(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            main(["queue", "delete", "some-id"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("--yes", stderr.getvalue())

    def test_publish_dry_run_does_not_require_credentials(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = main(["publish", "image.png", "caption", "--dry-run"])
        self.assertEqual(result, 0)
        self.assertIn("Dry run", stdout.getvalue())

    def test_recursive_frame_keeps_nested_output_paths_and_skips_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "source"
            (source / "nested").mkdir(parents=True)
            output = source / "rendered"
            output.mkdir()
            frame = folder / "frame.png"
            frame.write_bytes(b"frame")
            top_image = source / "top.jpg"
            nested_image = source / "nested" / "photo.webp"
            prior_output = output / "old.png"
            for image in (top_image, nested_image, prior_output):
                image.write_bytes(b"image")
            with (
                patch("src.cli.load_frame", return_value=object()),
                patch("src.cli.process_one", return_value=True) as process_one,
            ):
                result = image_cli_main(
                    [
                        str(frame),
                        str(source),
                        "--output",
                        str(output),
                        "--recursive",
                    ]
                )

            self.assertEqual(result, 0)
            output_paths = [call.args[2] for call in process_one.call_args_list]
            self.assertEqual(
                {path.relative_to(output).as_posix() for path in output_paths},
                {"top_framed.png", "nested/photo_framed.png"},
            )
            self.assertEqual(process_one.call_count, 2)


if __name__ == "__main__":
    unittest.main()
