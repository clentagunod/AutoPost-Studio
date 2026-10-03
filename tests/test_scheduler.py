"""Tests for AutoPost Studio sidecar persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.scheduler import PostRecord, delete_post_files, load_posts, save_post


class SchedulerStorageTests(unittest.TestCase):
    def test_round_trip_preserves_editable_post_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            expected = PostRecord(
                image=str(folder / "finished.png"),
                caption="A thoughtful caption",
                scheduled_at="2026-11-02 18:30",
                destination="facebook_page",
                page_id="123456",
                status="draft",
            )

            sidecar = save_post(expected, folder)
            actual = load_posts(folder)

            self.assertEqual(sidecar.suffix, ".autopost")
            self.assertEqual(actual, [expected])

    def test_corrupt_sidecar_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "bad.autopost").write_text("{", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "bad.autopost"):
                load_posts(folder)

    def test_sidecar_is_json_and_not_a_secret_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            post = PostRecord(image=str(folder / "image.png"), caption="Draft")
            path = save_post(post, folder)

            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["id"], post.id)
            self.assertNotIn("access_token", value)

    def test_image_without_sidecar_appears_as_a_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            photo = folder / "legacy-output.webp"
            photo.touch()

            posts = load_posts(folder)

            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0].image, str(photo.resolve()))
            self.assertEqual(posts[0].status, "draft")

    def test_bulk_delete_removes_sidecars_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            records = []
            for name in ("one.png", "two.png"):
                image = folder / name
                image.write_bytes(b"image")
                record = PostRecord(image=str(image), status="queued")
                save_post(record, folder)
                records.append(record)

            delete_post_files(records, folder)

            self.assertFalse((folder / "one.png").exists())
            self.assertFalse((folder / "two.png").exists())
            self.assertEqual(list(folder.glob("*.autopost")), [])
            self.assertEqual(load_posts(folder), [])

    def test_delete_preserves_image_referenced_by_another_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "shared.png"
            image.write_bytes(b"image")
            selected = PostRecord(image=str(image), caption="selected")
            other = PostRecord(image=str(image), caption="keep")
            save_post(selected, folder)
            save_post(other, folder)

            delete_post_files([selected], folder)

            self.assertTrue(image.exists())
            remaining = load_posts(folder)
            self.assertEqual([record.id for record in remaining], [other.id])

    def test_delete_refuses_post_currently_being_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            image = folder / "working.png"
            image.write_bytes(b"image")
            record = PostRecord(image=str(image), status="processing")
            save_post(record, folder)

            with self.assertRaisesRegex(OSError, "publishing"):
                delete_post_files([record], folder)

            self.assertTrue(image.exists())


if __name__ == "__main__":
    unittest.main()
