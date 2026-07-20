"""Conversational meal-planning agent (Pydantic AI + in-process MCP toolset).

The LLM interprets language and selects a business capability; every
state-changing workflow runs deterministically inside the MCP tools. The
session id is injected into tool calls by :func:`_inject_session_id` from
a ContextVar set by the API layer — the model can never target another
session, whatever arguments it produces.
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, RunContext
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ToolResult
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from recipes.mcp_server.meal_plan_tools import SESSION_TOOL_NAMES
from recipes.sessions.models import MAX_PLAN_DAYS

logger = logging.getLogger(__name__)

CHAT_MODEL_ENV_VAR = "CHAT_AGENT_MODEL"
DEFAULT_CHAT_MODEL = "openai:gpt-5-mini"
MODEL_TIMEOUT_ENV_VAR = "CHAT_MODEL_TIMEOUT_SECONDS"
DEFAULT_MODEL_TIMEOUT_SECONDS = 60.0

# Set per request by the chat API before agent.run(); read by the injector.
CURRENT_SESSION_ID: ContextVar[str] = ContextVar("chef_chat_session_id")

# Business tools whose session_id argument is always overwritten server-side;
# defined next to the tool registrations to prevent drift.
SESSION_TOOLS = SESSION_TOOL_NAMES

# Hard caps per user message: a planning turn needs at most a few tool
# round-trips; anything beyond this is a runaway loop.
CHAT_USAGE_LIMITS = UsageLimits(request_limit=8, tool_calls_limit=12)


class ChatOutput(BaseModel):
    """Final agent reply: full chat text plus a short spoken summary."""

    message: str
    voice_summary: str


CHAT_INSTRUCTIONS = f"""\
You are Chef in My Pocket, a conversational meal-planning assistant for a
Czech recipe collection.

Before creating a new meal plan, collect:
1. a supported dietary goal (high-protein, low-carb, vegetarian, vegan,
   pescatarian, gluten-free, dairy-free);
2. the number of days, between 1 and {MAX_PLAN_DAYS}.

The number of meals per day (1-3) is optional: when the user does not say,
plan 3 meals per day without asking. Any of these may arrive in any order
or in one message; remember what was already provided and never ask for it
again. Ask a clarification question only when required information is
genuinely missing or a requested meal cannot be identified safely.

Use the MCP tools for every recipe and shopping-list operation. Never
invent recipes, recipe IDs, ingredients, allergens, quantities, serving
sizes, or nutritional values.

Speak like a helpful cooking assistant, never like a technical system.
Do not mention datasets, databases, classifications, tools, servers,
sessions, indexes, or any other internals. When a caveat is needed, phrase
it in plain cooking terms — e.g. "these recipes don't list exact amounts,
so take 'high-protein' as approximate" — and only when it matters
(especially for high-protein and low-carb requests).

Plan days are numbered, not tied to calendar weekdays: day_index 0 =
day 1, 1 = day 2, and so on. Slots are the meals within a day, 0-based:
slot 0 = first meal, slot 1 = second, slot 2 = third. When the user
names a day ("day 2") or a meal ("the second meal of day 2"), resolve
it to the matching day_index and slot of the CURRENT plan yourself
before calling replace_meal; omit slot only when that day has a single
meal. If the user refers to a calendar weekday, ask which plan day they
mean unless it is unambiguous from the conversation. Pass a goal to replace_meal only
when the user asks for a different kind of dish (e.g. "something
vegetarian").

The plan is managed for you: create_meal_plan and replace_meal already
preserve other meals, avoid duplicate recipes, apply stored ingredient
exclusions and rebuild the whole shopping list. Use get_meal_plan when
the user wants to see the current plan; use add_ingredient_exclusion when
they want to avoid an ingredient (it only affects future searches — offer
to replace any currently affected meals, and say which days they are).

Never enumerate the shopping list's items in your reply — the user can
already see the complete, up-to-date shopping list alongside their plan.
Just say the shopping list is ready, or that it has been updated after a
change. For the meal plan itself: when a plan was just created (or the
user asks to see it), confirm it is done and then list every meal from
the tool response, day by day and slot by slot ("Day 1 — meal 1: …,
meal 2: …"; on single-meal days just "Day 1: …"). When one meal was
changed, do not re-list the whole plan — briefly explain what changed
(which day and meal, and the new recipe; name the old one if you know
it) and confirm the rest is untouched. Give ingredient details only
when the user explicitly asks.

If fewer meals could be planned than requested, or no recipes match,
explain that honestly in plain language and do not fabricate alternatives.
Use one-based day wording ("day 2", "the second meal of day 2") with
the user, never raw indexes. Recipes are Czech; answer in the user's
language.

Your reply has two fields: "message" (the full chat reply) and
"voice_summary" (what a voice assistant would say aloud). Write the
voice_summary as plain spoken sentences in the same language as the
reply — no markdown, no bullet lists, no recipe IDs. The listener may
be cooking hands-free, so the voice_summary must stand on its own,
never "see your screen". When a plan was created or the user asked to
hear it, say it is ready and then speak the entire plan, day by day and
meal by meal, e.g. "Your three-day high-protein plan is ready. Day one:
…, then …. Day two: …". When one meal was changed, say which day and
meal changed and what the new recipe is. When the reply is already
short (e.g. a clarifying question), reuse it verbatim.
"""


async def _inject_session_id(
    ctx: RunContext[Any],
    call_tool: CallToolFunc,
    name: str,
    args: dict[str, Any],
) -> ToolResult:
    """Overwrite session_id on business tools with the request's session."""
    if name in SESSION_TOOLS:
        try:
            session_id = CURRENT_SESSION_ID.get()
        except LookupError:
            raise RuntimeError(
                "chat agent used outside a request context: "
                "CURRENT_SESSION_ID must be set before running the agent"
            ) from None
        args = {**args, "session_id": session_id}
    return await call_tool(name, args)


def build_toolset(server: FastMCP) -> MCPToolset[None]:
    """In-process MCP toolset — no subprocess, no per-call server restarts."""
    return MCPToolset(
        server,
        id="chef-in-my-pocket",
        process_tool_call=_inject_session_id,
    )


def resolve_chat_model(cli_value: str | None = None) -> str:
    return cli_value or os.environ.get(CHAT_MODEL_ENV_VAR) or DEFAULT_CHAT_MODEL


def build_chat_agent(
    toolset: MCPToolset[None],
    *,
    model: Model | str | None = None,
) -> Agent[None, ChatOutput]:
    """Single conversational agent; structured output, MCP tool loop.

    ``NativeOutput`` keeps the final response a single JSON text message
    (no extra output-tool round trip per turn). ``defer_model_check=True``
    lets tests construct the agent with a scripted model and no provider
    credentials.
    """
    timeout = float(
        os.environ.get(MODEL_TIMEOUT_ENV_VAR, str(DEFAULT_MODEL_TIMEOUT_SECONDS))
    )
    return Agent(
        model if model is not None else resolve_chat_model(),
        instructions=CHAT_INSTRUCTIONS,
        output_type=NativeOutput(ChatOutput),
        toolsets=[toolset],
        retries=2,
        defer_model_check=True,
        model_settings=ModelSettings(timeout=timeout),
    )
