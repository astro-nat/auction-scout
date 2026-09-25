"""Gemini image understanding, over plain REST (the Interactions API).

One function: generate(prompt, image_bytes) -> the model's text. Raises on
any failure so the caller's retry wrapper counts the attempt — same
contract as the Anthropic client path in workers/enrich.

The first cut of this file targeted v1beta generateContent with the
gemini-2.5 model family; both are gone (the models 404), so this speaks
the current surface: POST /v1beta/interactions with a typed `input` array,
per ai.google.dev/gemini-api/docs (2026-09). thinking_level minimal —
this is structured extraction, not pondering. The response text is dug
out defensively because the REST field layout (output_text vs output
blocks vs steps) varies by response complexity.

REST rather than the google-genai SDK on purpose: the worker needs exactly
one call, and a dependency saves nothing while adding a package to every
image build.
"""

import base64

import httpx

from .. import config

_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _extract_text(data: dict) -> str:
    """The generated text, wherever this response shape put it."""
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"]
    chunks: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            t = node.get("text")
            if isinstance(t, str) and node.get("type") in (None, "text", "output_text"):
                chunks.append(t)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for key in ("output", "steps", "content"):
        if key in data:
            walk(data[key])
            if chunks:
                return "".join(chunks)
    raise ValueError(f"no text in interaction response (keys: {sorted(data)[:8]})")


def generate(prompt: str, image_bytes: bytes | list[bytes] | None = None,
             max_tokens: int = 800, mime_type: str = "image/jpeg") -> str:
    # max_tokens is accepted for signature parity with the Claude path but
    # not sent: the Interactions field name for it is unverified, and
    # thinking_level minimal plus bounded prompts keep outputs tight anyway.
    del max_tokens
    parts: list[dict] = [{"type": "text", "text": prompt}]
    # One image or several: a lot's later photos are where the damage the
    # first (often stock) photo never showed turns up.
    images = ([] if not image_bytes
              else image_bytes if isinstance(image_bytes, list) else [image_bytes])
    for img in images:
        parts.append({"type": "image", "mime_type": mime_type,
                      "data": base64.b64encode(img).decode()})
    r = httpx.post(
        _URL,
        headers={"x-goog-api-key": config.GEMINI_API_KEY,
                 "Content-Type": "application/json"},
        json={"model": config.GEMINI_MODEL,
              "input": parts,
              "generation_config": {"thinking_level": "minimal"}},
        timeout=60.0)
    r.raise_for_status()
    return _extract_text(r.json())
