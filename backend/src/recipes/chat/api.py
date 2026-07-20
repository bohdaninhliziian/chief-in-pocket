"""HTTP chat interface for the conversational agent.

One FastAPI app owns the whole runtime: dependencies (repository,
services, session store), the in-process MCP server, the toolset and the
agent are wired exactly once in the lifespan. The POST /chat handler only
orchestrates load-session → agent.run → persist; canonical state changes
happen inside the MCP business tools.

Sessions are in-memory (MVP): everything is lost when the process
restarts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from pydantic_ai.exceptions import AgentRunError, UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import Model

from recipes.chat.agent import (
    CHAT_USAGE_LIMITS,
    CURRENT_SESSION_ID,
    build_chat_agent,
    build_toolset,
)
from recipes.chat.history import history_limit, trim_history
from recipes.chat.synthesis import (
    AUDIO_CONTENT_TYPE,
    ElevenLabsSpeechSynthesizer,
    SpeechSynthesizer,
    SynthesisError,
)
from recipes.chat.transcription import (
    ELEVENLABS_API_KEY_ENV_VAR,
    MAX_AUDIO_BYTES,
    MIN_AUDIO_BYTES,
    ElevenLabsSpeechTranscriber,
    SpeechTranscriber,
    TranscriptionError,
)
from recipes.exceptions import RecipeNotFound
from recipes.mcp_server.dependencies import build_dependencies
from recipes.mcp_server.models import MealPlanResponse, RecipeDetail
from recipes.mcp_server.server import create_server
from recipes.sessions import SessionStore

logger = logging.getLogger(__name__)

CORS_ORIGINS_ENV_VAR = "CHAT_CORS_ORIGINS"
# Local development default: any localhost port (Vite picks the next free
# one when 5173 is taken). Deployments set CHAT_CORS_ORIGINS explicitly.
LOCALHOST_ORIGIN_REGEX = r"http://(localhost|127\.0\.0\.1):\d+"


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be empty")
        return value


class ChatResponse(BaseModel):
    session_id: str
    message: str
    voice_summary: str
    meal_plan: MealPlanResponse | None


class TranscriptResponse(BaseModel):
    text: str


# The voice summary speaks the full plan (the user may be cooking,
# hands-free), so the cap only guards the paid TTS API against runaway
# text; an extreme plan still gets clamped.
MAX_SPEAK_CHARS = 4000


class SpeakRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def _text_valid(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be empty")
        if len(value) > MAX_SPEAK_CHARS:
            raise ValueError(f"text must be at most {MAX_SPEAK_CHARS} characters")
        return value


def create_app(
    *,
    data_path: Path | None = None,
    model: Model | str | None = None,
    transcriber: SpeechTranscriber | None = None,
    synthesizer: SpeechSynthesizer | None = None,
) -> FastAPI:
    """Build the chat API; ``model``, ``transcriber`` and ``synthesizer`` let tests
    inject scripted implementations."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        deps = build_dependencies(data_path)
        server = create_server(dependencies=deps)
        agent = build_chat_agent(build_toolset(server), model=model)
        app.state.store = deps.session_store
        app.state.deps = deps
        app.state.agent = agent
        app.state.history_max = history_limit()  # resolved once, not per request
        # Voice is optional: without a key the endpoints report 503 instead
        # of failing the whole app at startup. ElevenLabs-only (per the
        # product brief); a fallback vendor would slot in behind the
        # SpeechTranscriber protocol.
        if transcriber is not None:
            app.state.transcriber = transcriber
        elif os.environ.get(ELEVENLABS_API_KEY_ENV_VAR):
            app.state.transcriber = ElevenLabsSpeechTranscriber()
            logger.info("voice input: ElevenLabs STT")
        else:
            app.state.transcriber = None
            logger.warning(
                "voice input disabled: %s not set", ELEVENLABS_API_KEY_ENV_VAR
            )
        # Voice output mirrors voice input: optional, ElevenLabs-only.
        if synthesizer is not None:
            app.state.synthesizer = synthesizer
        elif os.environ.get(ELEVENLABS_API_KEY_ENV_VAR):
            app.state.synthesizer = ElevenLabsSpeechSynthesizer()
            logger.info("voice output: ElevenLabs TTS")
        else:
            app.state.synthesizer = None
            logger.warning(
                "voice output disabled: %s not set", ELEVENLABS_API_KEY_ENV_VAR
            )
        # Keeps the in-process MCP toolset connected for the app's lifetime
        # instead of reconnecting on every request.
        async with agent:
            logger.info("chat API ready")
            yield

    app = FastAPI(title="Chef in My Pocket — chat", lifespan=lifespan)
    cors_env = os.environ.get(CORS_ORIGINS_ENV_VAR)
    if cors_env:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in cors_env.split(",") if o.strip()],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=LOCALHOST_ORIGIN_REGEX,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Serializes concurrent requests to the same session so a slow run
    # cannot clobber state saved by a later one (last-writer-wins race).
    session_locks: dict[str, asyncio.Lock] = {}

    def lock_for(session_id: str) -> asyncio.Lock:
        return session_locks.setdefault(session_id, asyncio.Lock())

    @app.post("/chat")
    async def chat(request: ChatRequest) -> ChatResponse:
        store: SessionStore = app.state.store
        started = time.perf_counter()
        created = await store.get_or_create(request.session_id)
        session_id = created.state.session_id
        async with lock_for(session_id):
            return await _handle_chat(store, session_id, request.message, started)

    async def _handle_chat(
        store: SessionStore, session_id: str, message: str, started: float
    ) -> ChatResponse:
        session = await store.get(session_id)
        if session is None:  # pragma: no cover - store owns the id we just used
            raise HTTPException(status_code=500, detail="session lost")
        # History lifecycle (1/3): what the model will see as prior context.
        logger.info(
            "session %s: history load — %d stored message(s) passed to the agent",
            session_id,
            len(session.history),
        )
        token = CURRENT_SESSION_ID.set(session_id)
        try:
            result = await app.state.agent.run(
                message,
                message_history=session.history,
                usage_limits=CHAT_USAGE_LIMITS,
            )
        except UsageLimitExceeded:
            logger.warning("session %s: usage limit reached", session_id)
            raise HTTPException(
                status_code=502,
                detail=(
                    "The assistant needed too many steps for this request. "
                    "Please try a simpler message."
                ),
            ) from None
        except AgentRunError as exc:
            # Model/tool failure mid-run: stored state was only ever updated
            # by fully-validated tool workflows, so the last valid plan
            # survives untouched.
            logger.error("session %s: agent run failed: %s", session_id, exc)
            raise HTTPException(
                status_code=502,
                detail="The assistant is temporarily unavailable. Please try again.",
            ) from None
        finally:
            CURRENT_SESSION_ID.reset(token)

        updated = await store.get(session_id)
        if updated is None:  # pragma: no cover - store owns the id we just used
            raise HTTPException(status_code=500, detail="session lost")
        # History lifecycle (2/3): this run's new messages, appended exactly
        # once — result.new_messages() excludes the history we passed in,
        # which is what prevents duplication across turns.
        new_messages = result.new_messages()
        logger.info(
            "session %s: history append — agent run produced %d new message(s)",
            session_id,
            len(new_messages),
        )
        if logger.isEnabledFor(logging.DEBUG):
            for index, msg in enumerate(new_messages):
                logger.debug(
                    "session %s: new message %d/%d %s [%s]",
                    session_id,
                    index + 1,
                    len(new_messages),
                    type(msg).__name__,
                    ", ".join(part.part_kind for part in msg.parts),
                )
        combined = [*session.history, *new_messages]
        updated.history = trim_history(combined, app.state.history_max)
        # History lifecycle (3/3): what actually gets persisted.
        logger.info(
            "session %s: history store — %d + %d new -> %d persisted (cap %d)",
            session_id,
            len(session.history),
            len(new_messages),
            len(updated.history),
            app.state.history_max,
        )
        await store.save(updated)

        usage = result.usage
        logger.info(
            "session %s: %d model request(s), %d tool call(s), %.1f ms",
            session_id,
            usage.requests,
            usage.tool_calls,
            (time.perf_counter() - started) * 1000,
        )
        meal_plan = (
            MealPlanResponse.from_state(updated.state) if updated.state.meals else None
        )
        output = result.output
        return ChatResponse(
            session_id=session_id,
            message=output.message,
            voice_summary=output.voice_summary[:MAX_SPEAK_CHARS],
            meal_plan=meal_plan,
        )

    @app.get("/sessions/{session_id}")
    async def get_session_state(session_id: str) -> MealPlanResponse:
        """Development helper: inspect a session's structured state."""
        store: SessionStore = app.state.store
        session = await store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown session")
        return MealPlanResponse.from_state(session.state)

    @app.get("/sessions/{session_id}/history")
    async def get_session_history(session_id: str) -> JSONResponse:
        """Development helper: the session's raw message transcript.

        Serializes the stored pydantic-ai message history — user prompts,
        assistant replies and every tool call/return in between — so the
        conversational memory can be inspected turn by turn.
        """
        store: SessionStore = app.state.store
        session = await store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="unknown session")
        messages = ModelMessagesTypeAdapter.dump_python(session.history, mode="json")
        return JSONResponse(
            {
                "session_id": session_id,
                "message_count": len(session.history),
                "messages": messages,
            }
        )

    @app.get("/recipes/{recipe_id}")
    async def get_recipe_detail(recipe_id: int) -> RecipeDetail:
        """Full recipe view for the meal-plan panel's expand feature."""
        try:
            recipe = app.state.deps.recipe_service.get_recipe(recipe_id)
        except RecipeNotFound:
            raise HTTPException(status_code=404, detail="unknown recipe") from None
        return RecipeDetail.from_recipe(recipe)

    @app.post("/transcribe")
    async def transcribe(audio: UploadFile) -> TranscriptResponse:
        """Voice input adapter: audio in, transcript out.

        The transcript is returned to the client, edited by the user and
        sent through the normal /chat endpoint — voice never gets its own
        conversation flow.
        """
        active: SpeechTranscriber | None = app.state.transcriber
        if active is None:
            raise HTTPException(
                status_code=503,
                detail="Voice input is not configured on this server.",
            )
        payload = await audio.read()
        if not payload:
            raise HTTPException(status_code=422, detail="empty audio upload")
        if len(payload) < MIN_AUDIO_BYTES:
            raise HTTPException(status_code=422, detail="recording too short")
        if len(payload) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="audio upload too large")
        started = time.perf_counter()
        try:
            text = await active.transcribe(
                payload,
                filename=audio.filename or "voice-message.webm",
                content_type=audio.content_type or "audio/webm",
            )
        except TranscriptionError:
            raise HTTPException(
                status_code=502,
                detail="Could not transcribe the recording. Please try again.",
            ) from None
        logger.info(
            "transcribed voice input in %.1f ms",
            (time.perf_counter() - started) * 1000,
        )
        return TranscriptResponse(text=text)

    @app.post("/speak")
    async def speak(request: SpeakRequest) -> Response:
        """Voice output: short text in, mp3 audio out. Nothing is stored;
        the client caches the audio per message, so replays are free."""
        active: SpeechSynthesizer | None = app.state.synthesizer
        if active is None:
            raise HTTPException(
                status_code=503,
                detail="Voice output is not configured on this server.",
            )
        started = time.perf_counter()
        try:
            audio = await active.synthesize(request.text)
        except SynthesisError:
            raise HTTPException(
                status_code=502,
                detail="Could not generate audio. Please try again.",
            ) from None
        logger.info(
            "synthesized voice output in %.1f ms",
            (time.perf_counter() - started) * 1000,
        )
        return Response(content=audio, media_type=AUDIO_CONTENT_TYPE)

    return app
