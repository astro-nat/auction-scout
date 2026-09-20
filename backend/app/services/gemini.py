"""Gemini image understanding, over plain REST.

One function: generate(prompt, image_bytes) -> the model's text. Raises on
any failure so the caller's retry wrapper counts the attempt — same
contract as the Anthropic client path in workers/enrich.

REST rather than the google-genai SDK on purpose: the worker needs exactly
one endpoint, and a dependency saves nothing while adding a package to
every image build. responseMimeType pins the output to JSON (the prompts
all ask for JSON), and thinking is disabled — this is structured
extraction, and thinking tokens would eat the output budget at ~50x the
value they add here.
"""

import base64

import httpx

from .. import config

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def generate(prompt: str, image_bytes: bytes | None = None,
             max_tokens: int = 800) -> str:
    parts = []
    if image_bytes:
        parts.append({"inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(image_bytes).decode()}})
    parts.append({"text": prompt})
    r = httpx.post(
        _URL.format(model=config.GEMINI_MODEL),
        headers={"x-goog-api-key": config.GEMINI_API_KEY,
                 "Content-Type": "application/json"},
        json={"contents": [{"parts": parts}],
              "generationConfig": {"maxOutputTokens": max_tokens,
                                   "responseMimeType": "application/json",
                                   "thinkingConfig": {"thinkingBudget": 0}}},
        timeout=60.0)
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]
