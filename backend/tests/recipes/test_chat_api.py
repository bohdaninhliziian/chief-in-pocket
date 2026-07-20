"""End-to-end tests for the chat API: FastAPI + agent + in-process MCP.

The model is a scripted ``FunctionModel`` standing in for the LLM; every
other layer (toolset, MCP server, services, repository, session store) is
real. The canonical three-message conversation from the spec is exercised
against one session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import make_enriched, write_enriched_json
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from recipes.chat.api import MAX_SPEAK_CHARS, create_app
from recipes.chat.synthesis import SynthesisError
from recipes.chat.transcription import (
    ElevenLabsSpeechTranscriber,
    TranscriptionError,
)
from recipes.classification import DietaryGoal

HP = DietaryGoal.HIGH_PROTEIN

RECIPES = [
    make_enriched(10, "Guláš", ["Hovězí plec", "Cibule"], goals=[HP]),
    make_enriched(14, "Kuřecí polévka", ["Kuřecí prsa", "Zázvor"], goals=[HP]),
    make_enriched(20, "Losos", ["Losos", "Citron"], goals=[HP]),
    make_enriched(25, "Vegetariánské rizoto", ["Rýže", "Sýr"], goals=[DietaryGoal.VEGETARIAN]),
]


def final(message: str, voice_summary: str | None = None) -> ModelResponse:
    """Scripted final reply in the agent's native structured-output format."""
    return ModelResponse(
        parts=[
            TextPart(
                content=json.dumps(
                    {"message": message, "voice_summary": voice_summary or message}
                )
            )
        ]
    )


def latest_user_prompt(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    return ""


def all_user_prompts(messages: list[ModelMessage]) -> str:
    prompts: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            prompts.extend(
                str(part.content)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            )
    return " ".join(prompts)


def scripted_planner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Deterministic stand-in for the LLM in the 3-message conversation."""
    last = messages[-1]
    if isinstance(last, ModelRequest) and any(
        isinstance(part, ToolReturnPart) for part in last.parts
    ):
        return final("Here is your updated plan.", "Your plan is updated — see your screen.")

    prompt = latest_user_prompt(messages)
    if "high-protein meals" in prompt:
        return final("How many days should I plan for?")
    if "Three days" in prompt:
        # goal must come from earlier conversation context
        assert "high-protein" in all_user_prompts(messages)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="create_meal_plan",
                    args={"goal": "high-protein", "days": 3, "meals_per_day": 1},
                )
            ]
        )
    if "Replace day 2" in prompt:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="replace_meal",
                    args={"day_index": 1, "goal": "vegetarian"},
                )
            ]
        )
    return final("I did not understand.")


@pytest.fixture
def app(tmp_path: Path) -> Any:
    data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
    return create_app(data_path=data_path, model=FunctionModel(scripted_planner))


def test_full_conversation_one_session(app: Any) -> None:
    with TestClient(app) as client:
        # 1 — goal only: agent asks for the number of days, no plan yet
        first = client.post("/chat", json={"message": "I want high-protein meals."})
        assert first.status_code == 200
        body = first.json()
        session_id = body["session_id"]
        assert session_id
        assert "How many days" in body["message"]
        assert body["voice_summary"] == "How many days should I plan for?"
        assert body["meal_plan"] is None

        # 2 — days only: goal survives from message 1, plan is created
        second = client.post(
            "/chat", json={"session_id": session_id, "message": "Three days."}
        )
        assert second.status_code == 200
        plan = second.json()["meal_plan"]
        assert plan["dietary_goal"] == "high-protein"
        assert plan["requested_days"] == 3
        assert [m["recipe_id"] for m in plan["meals"]] == [10, 14, 20]
        assert [m["day_label"] for m in plan["meals"]] == [
            "Day 1",
            "Day 2",
            "Day 3",
        ]
        assert plan["shopping_list"]
        assert second.json()["voice_summary"] == "Your plan is updated — see your screen."

        # 3 — replace day 2 with a vegetarian meal
        third = client.post(
            "/chat",
            json={
                "session_id": session_id,
                "message": "Replace day 2 with something vegetarian.",
            },
        )
        assert third.status_code == 200
        updated = third.json()["meal_plan"]
        ids = [m["recipe_id"] for m in updated["meals"]]
        assert ids == [10, 25, 20]  # days 1 & 3 preserved, day 2 swapped
        assert len(ids) == len(set(ids))
        assert updated["meals"][1]["recipe_name"] == "Vegetariánské rizoto"
        assert updated["dietary_goal"] == "high-protein"  # plan goal unchanged
        shopping_recipes = {
            rid for item in updated["shopping_list"] for rid in item["recipes"]
        }
        assert shopping_recipes == {10, 25, 20}  # rebuilt for the whole plan
        ingredients = {item["ingredient"] for item in updated["shopping_list"]}
        assert "Kuřecí prsa" not in ingredients  # old day-2 recipe gone
        assert "Rýže" in ingredients  # new day-2 recipe present

        # structured state is served by the dev endpoint too
        state = client.get(f"/sessions/{session_id}")
        assert state.status_code == 200
        assert [m["recipe_id"] for m in state.json()["meals"]] == [10, 25, 20]


def test_voice_summary_is_clamped_to_speak_cap(tmp_path: Path) -> None:
    """A verbose model could emit a voice_summary longer than /speak's cap;
    /chat clamps it so the frontend never gets a summary /speak would 422 on."""

    def verbose_summary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return final("Dlouhá odpověď.", "x" * (MAX_SPEAK_CHARS + 100))

    data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
    app = create_app(data_path=data_path, model=FunctionModel(verbose_summary))
    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "ahoj"})
    assert response.status_code == 200
    assert len(response.json()["voice_summary"]) == MAX_SPEAK_CHARS


def test_history_grows_without_duplication(app: Any) -> None:
    with TestClient(app) as client:
        first = client.post("/chat", json={"message": "I want high-protein meals."})
        session_id = first.json()["session_id"]
        client.post("/chat", json={"session_id": session_id, "message": "Three days."})
        client.post(
            "/chat",
            json={
                "session_id": session_id,
                "message": "Replace day 2 with something vegetarian.",
            },
        )

        store = app.state.store
        session = store._sessions[session_id]  # white-box: inspect stored history
        user_turns = [
            part
            for message in session.history
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        assert len(user_turns) == 3  # exactly one per request — no duplication
        # turn shapes: 2 messages for the text-only turn, 4 for each tool turn
        assert len(session.history) == 10


def test_sessions_are_isolated(app: Any) -> None:
    with TestClient(app) as client:
        first = client.post("/chat", json={"message": "I want high-protein meals."})
        second = client.post("/chat", json={"message": "I want high-protein meals."})
        one, two = first.json()["session_id"], second.json()["session_id"]
        assert one != two
        client.post("/chat", json={"session_id": one, "message": "Three days."})
        assert client.get(f"/sessions/{one}").json()["meals"]
        assert client.get(f"/sessions/{two}").json()["meals"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {"message": "   "},
        {},
        {"session_id": "x"},
    ],
)
def test_invalid_requests_rejected(app: Any, payload: dict[str, Any]) -> None:
    with TestClient(app) as client:
        assert client.post("/chat", json=payload).status_code == 422


def test_unknown_session_state_is_404(app: Any) -> None:
    with TestClient(app) as client:
        assert client.get("/sessions/does-not-exist").status_code == 404


class TestSessionHistory:
    def test_exposes_full_transcript_with_tool_calls(self, app: Any) -> None:
        with TestClient(app) as client:
            first = client.post("/chat", json={"message": "I want high-protein meals."})
            session_id = first.json()["session_id"]
            client.post(
                "/chat", json={"session_id": session_id, "message": "Three days."}
            )
            response = client.get(f"/sessions/{session_id}/history")
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == session_id
        assert body["message_count"] == len(body["messages"]) == 6
        dumped = str(body["messages"])
        assert "I want high-protein meals." in dumped  # user prompt
        assert "How many days should I plan for?" in dumped  # assistant text
        assert "create_meal_plan" in dumped  # tool call preserved

    def test_history_grows_across_turns(self, app: Any) -> None:
        with TestClient(app) as client:
            first = client.post("/chat", json={"message": "I want high-protein meals."})
            session_id = first.json()["session_id"]
            after_one = client.get(f"/sessions/{session_id}/history").json()
            client.post(
                "/chat", json={"session_id": session_id, "message": "Three days."}
            )
            after_two = client.get(f"/sessions/{session_id}/history").json()
        assert after_one["message_count"] == 2  # user prompt + text reply
        assert after_two["message_count"] == 6  # + tool-call round trip

    def test_unknown_session_is_404(self, app: Any) -> None:
        with TestClient(app) as client:
            assert client.get("/sessions/nope/history").status_code == 404


class TestRecipeDetail:
    def test_returns_full_recipe(self, app: Any) -> None:
        with TestClient(app) as client:
            response = client.get("/recipes/10")
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Guláš"
        assert body["ingredients"] == ["Hovězí plec", "Cibule"]
        assert body["instructions"]

    def test_unknown_recipe_is_404(self, app: Any) -> None:
        with TestClient(app) as client:
            assert client.get("/recipes/999").status_code == 404


class FakeTranscriber:
    def __init__(self, text: str = "Chci vegetariánský plán na tři dny") -> None:
        self.text = text
        self.calls: list[tuple[int, str, str]] = []

    async def transcribe(
        self, audio: bytes, *, filename: str, content_type: str
    ) -> str:
        self.calls.append((len(audio), filename, content_type))
        return self.text


# Big enough to pass the too-short guard; real spoken webm blobs are tens of KB.
VOICE_BLOB = b"fake-audio-bytes" * 256


class TestTranscribe:
    def make_app(self, tmp_path: Path, transcriber: Any) -> Any:
        data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
        return create_app(
            data_path=data_path,
            model=FunctionModel(scripted_planner),
            transcriber=transcriber,
        )

    def test_returns_transcript(self, tmp_path: Path) -> None:
        fake = FakeTranscriber()
        with TestClient(self.make_app(tmp_path, fake)) as client:
            response = client.post(
                "/transcribe",
                files={"audio": ("voice.webm", VOICE_BLOB, "audio/webm")},
            )
        assert response.status_code == 200
        assert response.json() == {"text": "Chci vegetariánský plán na tři dny"}
        assert fake.calls == [(len(VOICE_BLOB), "voice.webm", "audio/webm")]

    def test_empty_audio_rejected(self, tmp_path: Path) -> None:
        with TestClient(self.make_app(tmp_path, FakeTranscriber())) as client:
            response = client.post(
                "/transcribe", files={"audio": ("voice.webm", b"", "audio/webm")}
            )
        assert response.status_code == 422

    def test_tiny_audio_rejected_before_reaching_provider(
        self, tmp_path: Path
    ) -> None:
        fake = FakeTranscriber()
        with TestClient(self.make_app(tmp_path, fake)) as client:
            response = client.post(
                "/transcribe", files={"audio": ("voice.webm", b"x" * 32, "audio/webm")}
            )
        assert response.status_code == 422
        assert response.json()["detail"] == "recording too short"
        assert fake.calls == []

    def test_provider_failure_is_clean_502(self, tmp_path: Path) -> None:
        class FailingTranscriber:
            async def transcribe(
                self, audio: bytes, *, filename: str, content_type: str
            ) -> str:
                raise TranscriptionError("boom")

        with TestClient(self.make_app(tmp_path, FailingTranscriber())) as client:
            response = client.post(
                "/transcribe", files={"audio": ("voice.webm", VOICE_BLOB, "audio/webm")}
            )
        assert response.status_code == 502
        assert "transcribe" in response.json()["detail"]

    def test_unconfigured_voice_is_503(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
        app = create_app(data_path=data_path, model=FunctionModel(scripted_planner))
        with TestClient(app) as client:
            response = client.post(
                "/transcribe", files={"audio": ("voice.webm", b"x", "audio/webm")}
            )
        assert response.status_code == 503

    def test_elevenlabs_used_when_key_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test")
        data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
        app = create_app(data_path=data_path, model=FunctionModel(scripted_planner))
        with TestClient(app):
            assert isinstance(app.state.transcriber, ElevenLabsSpeechTranscriber)


class FakeSynthesizer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        return b"mp3-bytes"


class TestSpeak:
    def make_app(self, tmp_path: Path, synthesizer: Any) -> Any:
        data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
        return create_app(
            data_path=data_path,
            model=FunctionModel(scripted_planner),
            synthesizer=synthesizer,
        )

    def test_returns_audio(self, tmp_path: Path) -> None:
        fake = FakeSynthesizer()
        with TestClient(self.make_app(tmp_path, fake)) as client:
            response = client.post("/speak", json={"text": "Váš plán je připraven."})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.content == b"mp3-bytes"
        assert fake.calls == ["Váš plán je připraven."]

    @pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_SPEAK_CHARS + 1)])
    def test_invalid_text_rejected_before_reaching_provider(
        self, tmp_path: Path, text: str
    ) -> None:
        fake = FakeSynthesizer()
        with TestClient(self.make_app(tmp_path, fake)) as client:
            response = client.post("/speak", json={"text": text})
        assert response.status_code == 422
        assert fake.calls == []

    def test_provider_failure_is_clean_502(self, tmp_path: Path) -> None:
        class FailingSynthesizer:
            async def synthesize(self, text: str) -> bytes:
                raise SynthesisError("boom")

        with TestClient(self.make_app(tmp_path, FailingSynthesizer())) as client:
            response = client.post("/speak", json={"text": "Ahoj"})
        assert response.status_code == 502

    def test_unconfigured_voice_output_is_503(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
        app = create_app(data_path=data_path, model=FunctionModel(scripted_planner))
        with TestClient(app) as client:
            response = client.post("/speak", json={"text": "Ahoj"})
        assert response.status_code == 503
