"""Focused tests for image formats and Groq caption requests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from src.captions import generate_caption
from src.imaging import save_image


class ImageOutputTests(unittest.TestCase):
    def test_png_jpeg_and_webp_are_saved(self) -> None:
        image = Image.new("RGBA", (8, 8), (20, 80, 140, 255))
        with tempfile.TemporaryDirectory() as temporary:
            for suffix in (".png", ".jpg", ".webp"):
                destination = Path(temporary) / f"result{suffix}"
                save_image(image, destination, quality=80)
                with Image.open(destination) as result:
                    self.assertEqual(result.size, (8, 8))


class CaptionRequestTests(unittest.TestCase):
    @patch("src.captions.Groq")
    def test_groq_receives_text_only_keywords_and_template(self, groq: Mock) -> None:
        groq.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="A bright day for our community.")
                )
            ]
        )
        caption = generate_caption(
            {
                "caption_model": "llama-3.3-70b-versatile",
                "caption_keywords": "school, community",
                "caption_template": "{caption}\n{keywords}\n{hashtags}",
            },
            api_key="unit-test-token",
            existing_caption="Invite families to the open house.",
        )

        self.assertEqual(
            caption,
            "A bright day for our community.\nschool, community\n#school #community",
        )
        groq.assert_called_once_with(api_key="unit-test-token", timeout=90.0)
        request = groq.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "llama-3.3-70b-versatile")
        self.assertEqual(request["temperature"], 0.7)
        message = request["messages"][0]["content"]
        self.assertIsInstance(message, str)
        self.assertIn("school, community", message)
        self.assertIn("Invite families to the open house.", message)
        self.assertNotIn("image_url", str(request))
        self.assertNotIn("images", request)

    @patch("src.captions.requests.post")
    def test_openrouter_posts_to_provider_endpoint(self, post: Mock) -> None:
        post.return_value.ok = True
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": "A welcoming community."}}]
        }
        caption = generate_caption(
            {
                "caption_provider": "openrouter",
                "caption_models": {"openrouter": "openrouter/free"},
                "caption_endpoints": {"openrouter": "https://openrouter.ai/api/v1"},
                "caption_template": "{caption}",
            },
            api_key="router-key",
        )
        self.assertEqual(caption, "A welcoming community.")
        self.assertEqual(
            post.call_args.args[0],
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer router-key",
        )

    @patch("src.captions.genai.Client")
    def test_gemini_uses_sdk_text_generation(self, client: Mock) -> None:
        client.return_value.models.generate_content.return_value.text = "A great event."
        caption = generate_caption(
            {
                "caption_provider": "gemini",
                "caption_models": {"gemini": "gemini-model"},
                "caption_template": "{caption}",
            },
            api_key="gemini-key",
        )
        self.assertEqual(caption, "A great event.")
        client.assert_called_once_with(
            api_key="gemini-key", http_options={"timeout": 90000}
        )
        client.return_value.models.generate_content.assert_called_once()
        self.assertTrue(client.return_value.close.called)

    @patch("src.captions.requests.post")
    def test_ollama_uses_local_chat_without_api_key(self, post: Mock) -> None:
        post.return_value.ok = True
        post.return_value.json.return_value = {"message": {"content": "A joyful day."}}
        caption = generate_caption(
            {
                "caption_provider": "ollama",
                "caption_endpoints": {"ollama": "http://localhost:11434"},
                "caption_models": {"ollama": "gemma3:1b"},
                "caption_template": "{caption}",
            },
        )
        self.assertEqual(caption, "A joyful day.")
        self.assertEqual(post.call_args.args[0], "http://localhost:11434/api/chat")
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])
        self.assertFalse(post.call_args.kwargs["json"]["stream"])

    @patch("src.captions.Groq")
    def test_groq_caption_does_not_require_an_image(self, groq: Mock) -> None:
        groq.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="A great day."))]
        )
        caption = generate_caption(
            {
                "caption_model": "llama-3.3-70b-versatile",
                "caption_template": "{caption}",
            },
            api_key="unit-test-token",
        )
        self.assertEqual(caption, "A great day.")

    def test_groq_requires_api_key_before_request(self) -> None:
        with patch("src.captions.Groq") as groq:
            with self.assertRaisesRegex(ValueError, "Groq API key"):
                generate_caption({}, api_key="")
        groq.assert_not_called()

    def test_provider_api_keys_are_independently_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "OpenRouter API key"):
            generate_caption({"caption_provider": "openrouter"}, api_key="")
        with self.assertRaisesRegex(ValueError, "Gemini API key"):
            generate_caption({"caption_provider": "gemini"}, api_key="")
        with self.assertRaisesRegex(ValueError, "Unsupported caption provider"):
            generate_caption({"caption_provider": "unknown"}, api_key="key")


if __name__ == "__main__":
    unittest.main()
