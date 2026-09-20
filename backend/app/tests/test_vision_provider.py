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


# --- the Gemini transport ---------------------------------------------------

def _fake_post(capture, text='{"ok": true}', status=200):
    def post(url, headers=None, json=None, timeout=None):
        capture.update(url=url, headers=headers, body=json)
        req = httpx.Request("POST", url)
        payload = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        return httpx.Response(status, request=req, json=payload)
    return post


def test_generate_sends_image_prompt_and_settings(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
    cap = {}
    monkeypatch.setattr(gemini.httpx, "post", _fake_post(cap, text='{"a": 1}'))

    out = gemini.generate("identify this", b"\xff\xd8jpegbytes", max_tokens=400)
    assert out == '{"a": 1}'
    assert "gemini-2.5-flash:generateContent" in cap["url"]
    assert cap["headers"]["x-goog-api-key"] == "g-key"
    parts = cap["body"]["contents"][0]["parts"]
    assert "inline_data" in parts[0] and parts[1]["text"] == "identify this"
    gen = cap["body"]["generationConfig"]
    assert gen["maxOutputTokens"] == 400
    assert gen["responseMimeType"] == "application/json"
    assert gen["thinkingConfig"]["thinkingBudget"] == 0   # extraction, not pondering


def test_generate_raises_on_http_errors_so_retry_counts_it(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(gemini.httpx, "post", _fake_post({}, status=429))
    with pytest.raises(httpx.HTTPStatusError):
        gemini.generate("x", b"img")


# --- dispatch ---------------------------------------------------------------

def test_vision_json_routes_to_gemini_and_parses(monkeypatch):
    monkeypatch.setattr(config, "VISION_PROVIDER", "gemini")
    calls = []

    def fake_generate(prompt, image_bytes=None, max_tokens=800):
        calls.append({"prompt": prompt, "image": image_bytes,
                      "max_tokens": max_tokens})
        return json.dumps({"title": "Widget", "condition": "used"})

    monkeypatch.setattr(enrich.gemini, "generate", fake_generate)
    monkeypatch.setattr(
        enrich.client.messages, "create",
        lambda *a, **k: pytest.fail("claude called while provider is gemini"))

    out = enrich._vision_json("identify", b"img", max_tokens=400)
    assert out == {"title": "Widget", "condition": "used"}
    assert calls == [{"prompt": "identify", "image": b"img", "max_tokens": 400}]


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

    def flaky(prompt, image_bytes=None, max_tokens=800):
        attempts["n"] += 1
        raise RuntimeError("gemini down")

    monkeypatch.setattr(enrich.gemini, "generate", flaky)
    assert enrich._vision_json("x", b"img", max_tokens=400) is None
    assert attempts["n"] == enrich.MAX_ATTEMPTS
