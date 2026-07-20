"""Tests for the speech-to-text adapters.

All HTTP goes through ``httpx.MockTransport`` (ElevenLabs) — no test ever
contacts a real service.
"""

from __future__ import annotations

import httpx
import pytest

from recipes.chat.transcription import (
    ELEVENLABS_STT_URL,
    ElevenLabsSpeechTranscriber,
    TranscriptionError,
)


def make_transcriber(
    handler: httpx.MockTransport | None = None,
) -> ElevenLabsSpeechTranscriber:
    return ElevenLabsSpeechTranscriber(api_key="xi-test-key", transport=handler)


async def test_sends_audio_and_returns_stripped_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Asserting defaults: a developer's own env must not leak in.
    monkeypatch.delenv("CHAT_STT_LANGUAGE", raising=False)
    monkeypatch.delenv("ELEVENLABS_STT_MODEL", raising=False)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("xi-api-key")
        seen["body"] = request.read()
        return httpx.Response(200, json={"text": "  Chci vegetariánský plán  "})

    transcriber = make_transcriber(httpx.MockTransport(handler))
    text = await transcriber.transcribe(
        b"fake-audio", filename="voice.webm", content_type="audio/webm"
    )

    assert text == "Chci vegetariánský plán"
    assert seen["url"] == ELEVENLABS_STT_URL
    assert seen["api_key"] == "xi-test-key"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b"fake-audio" in body  # audio part uploaded
    assert b"scribe_v1" in body  # default model_id in the form data
    # Language pinned to Czech in the form data, never auto-detected.
    assert b'name="language_code"\r\n\r\ncs' in body


async def test_http_error_becomes_transcription_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"detail": "invalid api key"})
    )
    with pytest.raises(TranscriptionError, match="speech-to-text failed"):
        await make_transcriber(transport).transcribe(
            b"x", filename="voice.webm", content_type="audio/webm"
        )


async def test_network_error_becomes_transcription_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(TranscriptionError, match="speech-to-text failed"):
        await make_transcriber(httpx.MockTransport(handler)).transcribe(
            b"x", filename="voice.webm", content_type="audio/webm"
        )


def test_missing_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(TranscriptionError, match="ELEVENLABS_API_KEY"):
        ElevenLabsSpeechTranscriber()


def test_model_override_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELEVENLABS_STT_MODEL", "scribe_v2")
    transcriber = ElevenLabsSpeechTranscriber(api_key="xi-test-key")
    assert transcriber._model == "scribe_v2"


def test_language_override_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAT_STT_LANGUAGE", "sk")
    transcriber = ElevenLabsSpeechTranscriber(api_key="xi-test-key")
    assert transcriber._language == "sk"


