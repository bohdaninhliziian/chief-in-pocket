"""Text-to-speech adapter for voice output.

Voice output is on-demand: the frontend requests audio for one short
spoken summary at a time, and nothing is stored server-side. ElevenLabs
is the only provider — the product brief standardizes on ElevenLabs for
voice; if reliability ever demands it, a second vendor slots in behind
:class:`SpeechSynthesizer` without touching the API layer.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY_ENV_VAR = "ELEVENLABS_API_KEY"
ELEVENLABS_TTS_MODEL_ENV_VAR = "ELEVENLABS_TTS_MODEL"
DEFAULT_ELEVENLABS_TTS_MODEL = "eleven_flash_v2_5"
ELEVENLABS_VOICE_ID_ENV_VAR = "ELEVENLABS_VOICE_ID"
# Stock ElevenLabs premade voice ("Rachel"), multilingual-capable.
DEFAULT_ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# mp3 at 44.1 kHz / 128 kbps — broadly playable in browsers.
ELEVENLABS_OUTPUT_FORMAT = "mp3_44100_128"
TTS_TIMEOUT_SECONDS = 30.0
AUDIO_CONTENT_TYPE = "audio/mpeg"


class SynthesisError(Exception):
    """Text-to-speech failed (provider error or missing configuration)."""


class SpeechSynthesizer(Protocol):
    async def synthesize(self, text: str) -> bytes:
        """Return spoken audio (mp3 bytes) for the given text."""
        ...


class ElevenLabsSpeechSynthesizer:
    """ElevenLabs text-to-speech over the plain HTTP API.

    Reads ELEVENLABS_API_KEY from the environment unless a key is passed.
    ``transport`` exists so tests can inject an ``httpx.MockTransport`` —
    no test ever talks to the real service.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        key = api_key or os.environ.get(ELEVENLABS_API_KEY_ENV_VAR)
        if not key:
            raise SynthesisError(f"{ELEVENLABS_API_KEY_ENV_VAR} is not set")
        self._api_key = key
        self._model = (
            model
            or os.environ.get(ELEVENLABS_TTS_MODEL_ENV_VAR)
            or DEFAULT_ELEVENLABS_TTS_MODEL
        )
        self._voice_id = (
            voice_id
            or os.environ.get(ELEVENLABS_VOICE_ID_ENV_VAR)
            or DEFAULT_ELEVENLABS_VOICE_ID
        )
        self._transport = transport

    async def synthesize(self, text: str) -> bytes:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TTS_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    ELEVENLABS_TTS_URL.format(voice_id=self._voice_id),
                    headers={"xi-api-key": self._api_key},
                    params={"output_format": ELEVENLABS_OUTPUT_FORMAT},
                    json={"text": text, "model_id": self._model},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("elevenlabs synthesis failed: %s", exc)
            raise SynthesisError("text-to-speech failed") from exc
        audio = response.content
        logger.info(
            "elevenlabs synthesized %d chars -> %d bytes", len(text), len(audio)
        )
        return audio
