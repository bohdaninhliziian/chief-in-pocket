"""Speech-to-text adapters for voice input.

Voice is a thin input adapter: audio becomes text here, and the text goes
through the exact same chat flow as typed messages. Nothing downstream
knows the message was spoken.

Vendors sit behind :class:`SpeechTranscriber`, mirroring the other
protocol seams in this project. ElevenLabs (Scribe) is the only
implementation — the product brief standardizes on ElevenLabs for voice;
a second vendor can slot in behind the protocol purely for reliability.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

# STT models hallucinate on short unhinted audio (invented sentences,
# wrong-language output), so the expected language is pinned instead of
# auto-detected. The product is Czech-first; override via env if needed.
STT_LANGUAGE_ENV_VAR = "CHAT_STT_LANGUAGE"
DEFAULT_STT_LANGUAGE = "cs"

ELEVENLABS_API_KEY_ENV_VAR = "ELEVENLABS_API_KEY"
ELEVENLABS_STT_MODEL_ENV_VAR = "ELEVENLABS_STT_MODEL"
DEFAULT_ELEVENLABS_STT_MODEL = "scribe_v1"
ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
STT_TIMEOUT_SECONDS = 60.0

# MediaRecorder blobs for a spoken sentence are tens of KB; anything near
# this limit is not a chat message.
MAX_AUDIO_BYTES = 15 * 1024 * 1024

# ...and anything under ~1 KB is silence or a mis-fired recorder, which STT
# models turn into hallucinated text rather than an empty transcript.
MIN_AUDIO_BYTES = 1024


class TranscriptionError(Exception):
    """Speech-to-text failed (provider error or unusable audio)."""


class SpeechTranscriber(Protocol):
    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str
    ) -> str:
        """Return the transcript text for the given audio payload."""
        ...


class ElevenLabsSpeechTranscriber:
    """ElevenLabs Scribe speech-to-text over the plain HTTP API.

    Reads ELEVENLABS_API_KEY from the environment unless a key is passed.
    ``transport`` exists so tests can inject an ``httpx.MockTransport`` —
    no test ever talks to the real service.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        language: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        key = api_key or os.environ.get(ELEVENLABS_API_KEY_ENV_VAR)
        if not key:
            raise TranscriptionError(f"{ELEVENLABS_API_KEY_ENV_VAR} is not set")
        self._api_key = key
        self._model = (
            model
            or os.environ.get(ELEVENLABS_STT_MODEL_ENV_VAR)
            or DEFAULT_ELEVENLABS_STT_MODEL
        )
        self._language = (
            language or os.environ.get(STT_LANGUAGE_ENV_VAR) or DEFAULT_STT_LANGUAGE
        )
        self._transport = transport

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str
    ) -> str:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=STT_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    ELEVENLABS_STT_URL,
                    headers={"xi-api-key": self._api_key},
                    files={"file": (filename, audio, content_type)},
                    data={"model_id": self._model, "language_code": self._language},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.error("elevenlabs transcription failed: %s", exc)
            raise TranscriptionError("speech-to-text failed") from exc
        text = str(payload.get("text", "")).strip()
        logger.info(
            "elevenlabs transcribed %d bytes -> %d chars", len(audio), len(text)
        )
        return text


