"""Tests for Facebook Graph API request construction and image fitting."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from src.facebook import (
    FacebookError,
    _post,
    _prepare_upload,
    post_album,
    post_photo,
    validate_page_access,
)


class FacebookPostTests(unittest.TestCase):
    @patch("src.facebook.requests.get")
    def test_page_access_check_returns_page_name_without_posting(
        self, get: Mock
    ) -> None:
        get.return_value.ok = True
        get.return_value.json.return_value = {"id": "page-1", "name": "Example Page"}

        name = validate_page_access("page-1", "test-token")

        self.assertEqual(name, "Example Page")
        self.assertEqual(
            get.call_args.kwargs["params"],
            {"fields": "id,name", "access_token": "test-token"},
        )
        self.assertEqual(get.call_args.kwargs["timeout"], (10, 20))

    @patch("src.facebook.requests.get")
    def test_page_access_check_reports_wrong_page_or_invalid_token(
        self, get: Mock
    ) -> None:
        get.return_value.ok = False
        get.return_value.json.return_value = {
            "error": {"message": "Invalid OAuth access token.", "code": 190}
        }

        with self.assertRaisesRegex(FacebookError, "Invalid OAuth access token"):
            validate_page_access("page-1", "expired-token")

    @patch("src.facebook.requests.post")
    def test_deprecated_publish_actions_error_has_actionable_message(
        self, post: Mock
    ) -> None:
        post.return_value.ok = False
        post.return_value.text = "permission denied"
        post.return_value.json.return_value = {
            "error": {
                "code": 200,
                "message": "The permission(s) publish_actions are not available. It has been deprecated.",
            }
        }

        with self.assertRaisesRegex(
            FacebookError, "Create a new System User token"
        ) as error:
            _post("page-1/photos", "test-token", {"caption": "test"})

        self.assertIn("Do not request `publish_actions`", str(error.exception))

    @patch("src.facebook.requests.post")
    def test_local_photo_post_sends_page_credentials_and_caption(
        self, post: Mock
    ) -> None:
        post.return_value.ok = True
        post.return_value.json.return_value = {"id": "photo-42"}
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "photo.png"
            Image.new("RGB", (2, 2)).save(image_path)

            result = post_photo("page-1", "test-token", str(image_path), "Hello")

        self.assertEqual(result["id"], "photo-42")
        self.assertEqual(post.call_args.kwargs["data"]["access_token"], "test-token")
        self.assertEqual(post.call_args.kwargs["data"]["caption"], "Hello")
        self.assertEqual(post.call_args.kwargs["files"]["source"][2], "image/jpeg")

    @patch("src.facebook.requests.post")
    def test_scheduled_post_uses_meta_schedule_fields(self, post: Mock) -> None:
        post.return_value.ok = True
        post.return_value.json.return_value = {"id": "scheduled-7"}
        scheduled_at = datetime.now() + timedelta(hours=2)
        with tempfile.TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "photo.png"
            Image.new("RGB", (2, 2)).save(image_path)

            post_photo(
                "page-1",
                "test-token",
                str(image_path),
                "Later",
                scheduled_at,
            )

        data = post.call_args.kwargs["data"]
        self.assertEqual(data["published"], "false")
        self.assertEqual(data["scheduled_publish_time"], int(scheduled_at.timestamp()))

    @patch("src.facebook.requests.post")
    def test_album_uploads_unpublished_photos_then_attaches_them(
        self, post: Mock
    ) -> None:
        responses = []
        for value in ({"id": "photo-1"}, {"id": "photo-2"}, {"id": "post-1"}):
            response = Mock()
            response.ok = True
            response.json.return_value = value
            responses.append(response)
        post.side_effect = responses
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for index in range(2):
                image_path = Path(temporary) / f"photo-{index}.png"
                Image.new("RGB", (24, 20), (50 + index, 90, 120)).save(image_path)
                paths.append(str(image_path))

            result = post_album("page-1", "test-token", paths, "Album caption")

        self.assertEqual(result["id"], "post-1")
        self.assertEqual(post.call_count, 3)
        for call in post.call_args_list[:2]:
            self.assertTrue(call.args[0].endswith("/page-1/photos"))
            self.assertEqual(call.kwargs["data"]["published"], "false")
        feed_call = post.call_args_list[2]
        self.assertTrue(feed_call.args[0].endswith("/page-1/feed"))
        self.assertEqual(feed_call.kwargs["data"]["message"], "Album caption")
        self.assertEqual(
            feed_call.kwargs["data"]["attached_media[0]"],
            '{"media_fbid":"photo-1"}',
        )
        self.assertEqual(
            feed_call.kwargs["data"]["attached_media[1]"],
            '{"media_fbid":"photo-2"}',
        )

    @patch("src.facebook.requests.post")
    def test_scheduled_album_uploads_temporary_photos_and_schedules_feed(
        self, post: Mock
    ) -> None:
        responses = []
        for value in ({"id": "photo-1"}, {"id": "photo-2"}, {"id": "post-1"}):
            response = Mock()
            response.ok = True
            response.json.return_value = value
            responses.append(response)
        post.side_effect = responses
        scheduled_at = datetime.now() + timedelta(hours=2)
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for index in range(2):
                image_path = Path(temporary) / f"photo-{index}.png"
                Image.new("RGB", (24, 20), (90, 110, 130)).save(image_path)
                paths.append(str(image_path))

            post_album("page-1", "test-token", paths, "Scheduled album", scheduled_at)

        self.assertTrue(post.call_args_list[0].kwargs["data"]["temporary"])
        self.assertTrue(post.call_args_list[1].kwargs["data"]["temporary"])
        feed = post.call_args_list[2].kwargs["data"]
        self.assertEqual(feed["published"], "false")
        self.assertEqual(feed["unpublished_content_type"], "SCHEDULED")
        self.assertEqual(feed["scheduled_publish_time"], int(scheduled_at.timestamp()))

    def test_schedule_outside_meta_window_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "scheduled posts"):
            post_photo(
                "page-1",
                "test-token",
                "unused.png",
                schedule_at=datetime.now() + timedelta(days=31),
            )


class FacebookImageFitTests(unittest.TestCase):
    def test_facebook_upload_preserves_image_bounds_without_side_panels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "source.png"
            destination = folder / "upload.jpg"
            image = Image.new("RGB", (120, 100))
            for x in range(image.width):
                for y in range(image.height):
                    image.putpixel((x, y), (x, y, (x + y) % 256))
            image.save(source)

            _prepare_upload(source, destination)

            with Image.open(destination) as uploaded:
                self.assertEqual(uploaded.size, image.size)
                for point in ((0, 0), (119, 99)):
                    actual = uploaded.getpixel(point)
                    expected = image.getpixel(point)
                    self.assertTrue(
                        all(
                            abs(channel - source) <= 4
                            for channel, source in zip(actual, expected)
                        )
                    )
                self.assertEqual(uploaded.format, "JPEG")


if __name__ == "__main__":
    unittest.main()
