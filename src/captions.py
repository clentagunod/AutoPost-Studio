"""Text-only caption generation for supported AI providers."""

from __future__ import annotations

import re
from typing import Any

import requests
from google import genai
from google.genai.errors import APIError
from groq import Groq, GroqError

TIMEOUT = (5, 90)
PROVIDER_DEFAULTS = {
    "groq": {
        "label": "Groq · free tier",
        "endpoint": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "requires_api_key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "openrouter/free",
        "requires_api_key": True,
    },
    "gemini": {
        "label": "Gemini",
        "endpoint": "Google Gemini API (built in)",
        "model": "gemini-2.5-flash-lite",
        "requires_api_key": True,
    },
    "ollama": {
        "label": "Ollama",
        "endpoint": "http://localhost:11434",
        "model": "gemma3:1b",
        "requires_api_key": False,
    },
}


def generate_caption(
    preferences: dict[str, Any],
    api_key: str = "",
    existing_caption: str = "",
) -> str:
    provider = str(preferences.get("caption_provider", "groq")).strip().lower()
    defaults = PROVIDER_DEFAULTS.get(provider)
    if defaults is None:
        raise ValueError(f"Unsupported caption provider: {provider}")

    models = preferences.get("caption_models", {})
    endpoints = preferences.get("caption_endpoints", {})
    model = str(
        models.get(provider, preferences.get("caption_model", defaults["model"]))
        if isinstance(models, dict)
        else preferences.get("caption_model", defaults["model"])
    ).strip()
    endpoint = str(
        endpoints.get(provider, defaults["endpoint"])
        if isinstance(endpoints, dict)
        else defaults["endpoint"]
    ).strip().rstrip("/")
    keywords = str(preferences.get("caption_keywords", "")).strip()
    template = str(preferences.get("caption_template", "{caption}\n\n{hashtags}"))
    provider_name = {
        "groq": "Groq",
        "openrouter": "OpenRouter",
        "gemini": "Gemini",
        "ollama": "Ollama",
    }[provider]
    if not model:
        raise ValueError(f"Set a {provider_name} model in Options → Captions.")
    if defaults["requires_api_key"] and not api_key.strip():
        raise ValueError(f"Enter a {provider_name} API key in Options → Captions.")
    if provider in {"openrouter", "ollama"} and not endpoint:
        raise ValueError(f"Set the {defaults['label']} endpoint in Options → Captions.")

    prompt = (
        "Write one concise, friendly social-media caption using only the supplied "
        "keywords and user-provided context. Do not refer to or infer anything from "
        "an image. Follow the user's caption template where practical, but return "
        "only the caption text, without surrounding quotes.\n"
        f"Keywords: {keywords or 'none'}\n"
        f"Template: {template}\n"
        f"User-provided context: {existing_caption.strip() or 'none'}"
    )

    if provider == "groq":
        generated = _generate_groq(model, api_key, prompt)
    elif provider == "openrouter":
        generated = _generate_openrouter(endpoint, model, api_key, prompt)
    elif provider == "gemini":
        generated = _generate_gemini(model, api_key, prompt)
    else:
        generated = _generate_ollama(endpoint, model, prompt)
    return _apply_template(generated, keywords, template)


def _generate_groq(model: str, api_key: str, prompt: str) -> str:
    try:
        client = Groq(api_key=api_key.strip(), timeout=90.0)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
        finally:
            client.close()
    except GroqError as exc:
        raise RuntimeError(f"Groq caption request failed: {exc}") from exc
    try:
        text = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Groq response did not contain generated caption text."
        ) from exc
    return _extract_text(text, "Groq")


def _generate_openrouter(
    endpoint: str, model: str, api_key: str, prompt: str
) -> str:
    body = _post_json(
        f"{endpoint}/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        },
        {"Authorization": "Bearer " + api_key.strip()},
        "OpenRouter",
    )
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "OpenRouter response did not contain generated caption text."
        ) from exc
    return _extract_text(text, "OpenRouter")


def _generate_gemini(model: str, api_key: str, prompt: str) -> str:
    client = genai.Client(api_key=api_key.strip(), http_options={"timeout": 90000})
    try:
        response = client.models.generate_content(model=model, contents=prompt)
    except APIError as exc:
        raise RuntimeError(f"Gemini caption request failed: {exc}") from exc
    finally:
        client.close()
    return _extract_text(response.text, "Gemini")


def _generate_ollama(endpoint: str, model: str, prompt: str) -> str:
    body = _post_json(
        f"{endpoint}/api/chat",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
        {},
        "Ollama",
    )
    try:
        text = body["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            "Ollama response did not contain generated caption text."
        ) from exc
    return _extract_text(text, "Ollama")


def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], provider: str
) -> dict[str, Any]:
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", **headers},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"{provider} caption request failed: {exc}") from exc
    if not response.ok:
        detail = response.text.strip()
        if len(detail) > 1000:
            detail = detail[:1000] + "..."
        raise RuntimeError(
            f"{provider} returned HTTP {response.status_code}: "
            f"{detail or response.reason}"
        )
    try:
        body: Any = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{provider} returned invalid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"{provider} returned an unexpected response.")
    return body


def _extract_text(value: Any, provider: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{provider} returned an empty caption.")
    return value.strip()


def _apply_template(generated: str, keywords: str, template: str) -> str:
    tags = [
        "#" + tag
        for keyword in keywords.split(",")
        if (tag := re.sub(r"[^\w]", "", keyword.strip().lstrip("#")))
    ]
    try:
        return template.format(
            caption=generated, keywords=keywords, hashtags=" ".join(tags)
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid caption template: {exc}") from exc
