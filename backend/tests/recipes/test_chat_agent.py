"""Tests for the conversational chat agent over the in-process MCP toolset.

No real model provider is ever called: the model side is scripted with
``FunctionModel`` while the MCP toolset talks to the real in-process server.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_enriched, write_enriched_json
from mcp.server.fastmcp import FastMCP
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from recipes.chat import CURRENT_SESSION_ID, build_chat_agent, build_toolset
from recipes.chat.history import trim_history
from recipes.classification import DietaryGoal
from recipes.mcp_server.dependencies import build_dependencies
from recipes.mcp_server.server import create_server
from recipes.sessions import InMemorySessionStore

HP = DietaryGoal.HIGH_PROTEIN

RECIPES = [
    make_enriched(10, "Hovězí guláš", ["Hovězí plec", "Mléko"], goals=[HP]),
    make_enriched(14, "Kuřecí polévka", ["Kuřecí prsa", "Zázvor"], goals=[HP]),
    make_enriched(40, "Salát s tofu", ["Tofu", "Okurka"], goals=[DietaryGoal.VEGETARIAN]),
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


def make_server(tmp_path: Path, store: InMemorySessionStore) -> FastMCP:
    data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
    return create_server(dependencies=build_dependencies(data_path, session_store=store))


async def test_injector_covers_every_session_scoped_tool(tmp_path: Path) -> None:
    """Guard against drift: any registered tool taking session_id must be
    covered by the session-id injector, or the model could target other
    sessions."""
    from recipes.chat.agent import SESSION_TOOLS

    server = make_server(tmp_path, InMemorySessionStore())
    tools = await server.list_tools()
    session_scoped = {
        tool.name
        for tool in tools
        if "session_id" in (tool.inputSchema.get("properties") or {})
    }
    assert session_scoped == SESSION_TOOLS


async def test_in_process_toolset_executes_mcp_tool(tmp_path: Path) -> None:
    """Tracer bullet: an Agent with an in-process MCPToolset can call a real tool."""
    server = make_server(tmp_path, InMemorySessionStore())
    tool_returns: list[ToolReturnPart] = []

    def scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:  # first request: call the tool
            return ModelResponse(parts=[ToolCallPart(tool_name="list_supported_goals", args={})])
        for part in messages[-1].parts:
            if isinstance(part, ToolReturnPart):
                tool_returns.append(part)
        return ModelResponse(parts=[TextPart(content="done")])

    agent = Agent(FunctionModel(scripted), toolsets=[MCPToolset(server)])
    result = await agent.run("Which dietary goals do you support?")

    assert result.output == "done"
    assert len(tool_returns) == 1
    returned = str(tool_returns[0].content)
    assert "vegetarian" in returned and "high-protein" in returned


async def test_model_supplied_session_id_is_overridden(tmp_path: Path) -> None:
    """The injector must force the request's session id onto business tools."""
    store = InMemorySessionStore()
    server = make_server(tmp_path, store)
    await store.get_or_create("real-session")

    def scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="create_meal_plan",
                        # model tries to plant its own session id
                        args={"session_id": "evil", "goal": "high-protein", "days": 2},
                    )
                ]
            )
        return final("planned")

    agent = build_chat_agent(build_toolset(server), model=FunctionModel(scripted))
    token = CURRENT_SESSION_ID.set("real-session")
    try:
        result = await agent.run("Two days of high-protein meals please.")
    finally:
        CURRENT_SESSION_ID.reset(token)

    assert result.output.message == "planned"
    assert result.output.voice_summary == "planned"  # helper defaults to message
    real = await store.get("real-session")
    assert real is not None
    assert [m.recipe_id for m in real.state.meals] == [10, 14]
    assert await store.get("evil") is None  # the model's id never reached the store


async def test_tool_error_round_trips_as_retry(tmp_path: Path) -> None:
    """Domain errors surface as retry text the model can react to."""
    store = InMemorySessionStore()
    server = make_server(tmp_path, store)
    await store.get_or_create("s1")
    retry_texts: list[str] = []

    def scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:  # replace before any plan exists
            return ModelResponse(
                parts=[ToolCallPart(tool_name="replace_meal", args={"day_index": 0})]
            )
        for part in messages[-1].parts:
            if isinstance(part, RetryPromptPart):
                retry_texts.append(str(part.content))
        return final("explained to user")

    agent = build_chat_agent(build_toolset(server), model=FunctionModel(scripted))
    token = CURRENT_SESSION_ID.set("s1")
    try:
        result = await agent.run("Replace day 1 please.")
    finally:
        CURRENT_SESSION_ID.reset(token)

    assert result.output.message == "explained to user"
    assert any("no meal plan exists yet" in text for text in retry_texts)


def user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def text(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])


def tool_pair() -> list[ModelMessage]:
    return [
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="1")]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="t", content="ok", tool_call_id="1")]
        ),
    ]


class TestTrimHistory:
    def test_under_limit_unchanged(self) -> None:
        messages: list[ModelMessage] = [user("hi"), text("hello")]
        assert trim_history(messages, 10) == messages

    def test_drops_oldest_whole_turns(self) -> None:
        messages: list[ModelMessage] = [
            user("one"),
            text("a"),
            user("two"),
            text("b"),
            user("three"),
            text("c"),
        ]
        trimmed = trim_history(messages, 3)
        assert trimmed == messages[4:]  # cut lands on the "three" user turn

    def test_never_splits_tool_call_pairs(self) -> None:
        messages: list[ModelMessage] = [
            user("plan please"),
            *tool_pair(),
            text("planned"),
            user("replace day 2"),
            *tool_pair(),
            text("replaced"),
        ]
        trimmed = trim_history(messages, 3)
        # target cut is inside the second tool exchange; boundary rules move
        # the cut to the start of that user turn instead
        assert trimmed == messages[4:]

    def test_no_user_boundary_returns_unchanged(self) -> None:
        messages: list[ModelMessage] = [*tool_pair(), *tool_pair()]
        assert trim_history(messages, 2) == messages
