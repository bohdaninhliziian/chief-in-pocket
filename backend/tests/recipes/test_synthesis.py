"""Tests for the ElevenLabs text-to-speech adapter (no real HTTP ever)."""

from __future__ import annotations

import json

import httpx
import pytest

from recipes.chat.synthesis import (
    DEFAULT_ELEVENLABS_TTS_MODEL,
    DEFAULT_ELEVENLABS_VOICE_ID,
    ElevenLabsSpeechSynthesizer,
    SynthesisError,
)


def make_synthesizer(
    handler: object, **kwargs: str
) -> ElevenLabsSpeechSynthesizer:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return ElevenLabsSpeechSynthesizer(
        api_key="xi-test", transport=transport, **kwargs
    )


async def test_sends_text_and_returns_audio_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Asserting defaults: a developer's own env must not leak in.
    monkeypatch.delenv("ELEVENLABS_TTS_MODEL", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("xi-api-key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"mp3-bytes")

    synthesizer = make_synthesizer(handler)
    audio = await synthesizer.synthesize("Váš plán je připraven.")

    assert audio == b"mp3-bytes"
    assert DEFAULT_ELEVENLABS_VOICE_ID in str(captured["url"])
    assert "output_format=mp3_44100_128" in str(captured["url"])
    assert captured["key"] == "xi-test"
    assert captured["body"] == {
        "text": "Váš plán je připraven.",
        "model_id": DEFAULT_ELEVENLABS_TTS_MODEL,
    }


async def test_voice_and_model_overrides() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"x")

    synthesizer = make_synthesizer(handler, model="eleven_v3", voice_id="custom-voice")
    await synthesizer.synthesize("Ahoj")

    assert "custom-voice" in str(captured["url"])
    assert captured["body"] == {"text": "Ahoj", "model_id": "eleven_v3"}


async def test_http_error_becomes_synthesis_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad key"})

    synthesizer = make_synthesizer(handler)
    with pytest.raises(SynthesisError):
        await synthesizer.synthesize("Ahoj")


async def test_network_error_becomes_synthesis_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    synthesizer = make_synthesizer(handler)
    with pytest.raises(SynthesisError):
        await synthesizer.synthesize("Ahoj")


def test_missing_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(SynthesisError):
        ElevenLabsSpeechSynthesizer()
