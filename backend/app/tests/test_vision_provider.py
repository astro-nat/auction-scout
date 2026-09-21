"""The vision-provider switch: Gemini for image understanding when its key
is set, Claude otherwise, and the gold-check audit pinned to Claude either
way — a value priced by one model and audited by another is a genuinely
independent second opinion.
"""

import json

import httpx
import pytest

from app import config
from app.services import gemini
from app.workers import enrich


# --- provider resolution ----------------------------------------------------

def test_key_presence_is_the_switch(monkeypatch):
    monkeypatch.setattr(config, "VISION_PROVIDER", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert config.vision_provider() == "claude"
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g-key")
    assert config.vision_provider() == "gemini"


def test_explicit_provider_overrides_the_key(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(config, "VISION_PROVIDER", "Claude")
    assert config.vision_provider() == "claude"


# --- image format sniffing --------------------------------------------------

def test_mime_is_sniffed_from_magic_bytes():
    """Vinted serves webp; a mislabeled media_type is a rejected API call
    that fail-open turns into a silently blind vision pass."""
    assert enrich._image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert enrich._image_mime(b"\x89PNG\r\n\x1a\n rest") == "image/png"
    assert enrich._image_mime(b"GIF89a...") == "image/gif"
    assert enrich._image_mime(b"\xff\xd8\xff\xe0 jpeg") == "image/jpeg"
    assert enrich._image_mime(b"mystery bytes") == "image/jpeg"   # default


def test_gemini_carries_the_sniffed_mime(monkeypatch):
    cap = {}
    monkeypatch.setattr(gemini.httpx, "post", _fake_post(cap))
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g-key")
    gemini.generate("look", b"RIFFxxxxWEBP", mime_type="image/webp")
    assert cap["body"]["input"][1]["mime_type"] == "image/webp"


# --- the Gemini transport ---------------------------------------------------

def _fake_post(capture, payload=None, status=200):
    def post(url, headers=None, json=None, timeout=None):
        capture.update(url=url, headers=headers, body=json)
        req = httpx.Request("POST", url)
        return httpx.Response(status, request=req,
                              json=payload or {"output_text": "{}"})
    return post


def test_generate_speaks_the_interactions_api(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.6-flash")
    cap = {}
    monkeypatch.setattr(gemini.httpx, "post",
                        _fake_post(cap, payload={"output_text": '{"a": 1}'}))

    out = gemini.generate("identify this", b"\xff\xd8jpegbytes", max_tokens=400)
    assert out == '{"a": 1}'
    assert cap["url"].endswith("/v1beta/interactions")
    assert cap["headers"]["x-goog-api-key"] == "g-key"
    assert cap["body"]["model"] == "gemini-3.6-flash"
    parts = cap["body"]["input"]
    assert parts[0] == {"type": "text", "text": "identify this"}
    assert parts[1]["type"] == "image" and parts[1]["mime_type"] == "image/jpeg"
    assert cap["body"]["generation_config"]["thinking_level"] == "minimal"


def test_extract_text_handles_the_shapes_the_api_returns():
    assert gemini._extract_text({"output_text": "hi"}) == "hi"
    assert gemini._extract_text(
        {"output": [{"type": "text", "text": "a"},
                    {"type": "text", "text": "b"}]}) == "ab"
    assert gemini._extract_text(
        {"steps": [{"content": [{"type": "text", "text": "deep"}]}]}) == "deep"
    with pytest.raises(ValueError):
        gemini._extract_text({"id": "x", "usage": {}})


def test_generate_raises_on_http_errors_so_retry_counts_it(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(gemini.httpx, "post", _fake_post({}, status=429))
    with pytest.raises(httpx.HTTPStatusError):
        gemini.generate("x", b"img")


# --- dispatch ---------------------------------------------------------------

def test_vision_json_routes_to_gemini_and_parses(monkeypatch):
    monkeypatch.setattr(config, "VISION_PROVIDER", "gemini")
    calls = []

    def fake_generate(prompt, image_bytes=None, max_tokens=800,
                      mime_type="image/jpeg"):
        calls.append({"prompt": prompt, "image": image_bytes,
                      "max_tokens": max_tokens, "mime_type": mime_type})
        return json.dumps({"title": "Widget", "condition": "used"})

    monkeypatch.setattr(enrich.gemini, "generate", fake_generate)
    monkeypatch.setattr(
        enrich.client.messages, "create",
        lambda *a, **k: pytest.fail("claude called while provider is gemini"))

    out = enrich._vision_json("identify", b"img", max_tokens=400)
    assert out == {"title": "Widget", "condition": "used"}
    assert calls == [{"prompt": "identify", "image": b"img",
                      "max_tokens": 400, "mime_type": "image/jpeg"}]


def test_vision_json_stays_on_claude_by_default(monkeypatch):
    monkeypatch.setattr(config, "VISION_PROVIDER", "claude")
    monkeypatch.setattr(
        enrich.gemini, "generate",
        lambda *a, **k: pytest.fail("gemini called while provider is claude"))

    class _Resp:
        content = [type("T", (), {"text": '{"title": "W"}'})()]

    seen = {}

    def fake_create(**kw):
        seen.update(kw)
        return _Resp()

    monkeypatch.setattr(enrich.client.messages, "create", fake_create)
    out = enrich._vision_json("identify", b"img", max_tokens=400)
    assert out == {"title": "W"}
    assert seen["max_tokens"] == 400


def test_gemini_failure_retries_then_gives_up(monkeypatch):
    monkeypatch.setattr(config, "VISION_PROVIDER", "gemini")
    attempts = {"n": 0}

    def flaky(prompt, image_bytes=None, max_tokens=800,
              mime_type="image/jpeg"):
        attempts["n"] += 1
        raise RuntimeError("gemini down")

    monkeypatch.setattr(enrich.gemini, "generate", flaky)
    assert enrich._vision_json("x", b"img", max_tokens=400) is None
    assert attempts["n"] == enrich.MAX_ATTEMPTS
