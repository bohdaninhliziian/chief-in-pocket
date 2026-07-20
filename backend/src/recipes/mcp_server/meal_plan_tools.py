"""High-level business MCP tools for the conversational agent.

Each tool executes one complete deterministic workflow (search → assign →
shopping list → validate → persist) so the LLM only selects a capability
and supplies user intent — it never orchestrates low-level steps or
mutates state itself. Session state is written only after the new
``SessionState`` validated, so a failed operation always preserves the
previous valid plan.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from recipes.classification import DietaryGoal
from recipes.exceptions import NoMealPlan
from recipes.mcp_server.dependencies import Dependencies
from recipes.mcp_server.models import ExclusionResponse, MealPlanResponse
from recipes.mcp_server.tools import tool_call
from recipes.normalization import normalize_ingredient_key
from recipes.services.meal_plan_service import DEFAULT_MEALS_PER_DAY
from recipes.sessions import MAX_PLAN_DAYS, ChatSession, SessionState

logger = logging.getLogger(__name__)

# Single source of truth for session-scoped tools: the chat agent's
# session-id injector covers exactly these names, and a test asserts the
# set matches every registered tool that takes a session_id parameter.
SESSION_TOOL_NAMES = frozenset(
    {"create_meal_plan", "replace_meal", "get_meal_plan", "add_ingredient_exclusion"}
)

_SESSION_ID_DOC = "Chat session id. Supplied automatically by the application — do not invent one."


def register_meal_plan_tools(mcp: FastMCP, deps: Dependencies) -> None:
    async def _require_plan_session(session_id: str) -> ChatSession:
        session = await deps.session_store.get(session_id)
        if session is None or not session.state.meals:
            raise NoMealPlan("no meal plan exists yet; create one first")
        return session

    @mcp.tool(
        description=(
            "Create a complete meal plan for a chat session: searches "
            "recipes for the goal, assigns them deterministically to days "
            "(0-based day_index; day 0 is labelled 'Day 1' — plan days are "
            "numbered, never calendar weekdays) and slots (0-based meal of "
            "the day), builds the merged shopping list and saves everything "
            "as the session's current plan, replacing any previous plan. "
            "Stored ingredient exclusions are applied. If fewer recipes "
            "match than requested, later days get fewer meals — the "
            "response then has planned_meals < requested_meals; tell the "
            f"user honestly. Days must be 1-{MAX_PLAN_DAYS}; meals_per_day "
            f"1-3 (default {DEFAULT_MEALS_PER_DAY}). "
            f"session_id: {_SESSION_ID_DOC}"
        )
    )
    async def create_meal_plan(
        session_id: str,
        goal: DietaryGoal,
        days: int,
        meals_per_day: int = DEFAULT_MEALS_PER_DAY,
    ) -> MealPlanResponse:
        with tool_call("create_meal_plan"):
            session = await deps.session_store.get_or_create(session_id)
            build = deps.meal_plan_service.create_plan(
                goal=goal,
                days=days,
                meals_per_day=meals_per_day,
                excluded_ingredients=session.state.excluded_ingredients,
            )
            new_state = SessionState(
                session_id=session.state.session_id,
                dietary_goal=goal,
                number_of_days=days,
                meals_per_day=meals_per_day,
                meals=build.meals,
                excluded_ingredients=session.state.excluded_ingredients,
                shopping_list=build.shopping_list,
            )
            session.state = new_state
            await deps.session_store.save(session)
            logger.info(
                "session %s: plan created (%s, %d/%d days)",
                session_id,
                goal.value,
                build.planned_days,
                build.requested_days,
            )
            return MealPlanResponse.from_state(new_state)

    @mcp.tool(
        description=(
            "Replace exactly one meal in the session's current plan. "
            "Resolve day references to day_index yourself (day 1 = 0, "
            "day 2 = 1, …). slot picks the meal within the day (0-based: 0 = first "
            "meal); it may be omitted only when that day has a single "
            "meal. Pass goal only when the user wants a different dietary "
            "goal for the replacement (e.g. 'something vegetarian'). The "
            "server preserves every other meal, never picks a recipe "
            "already in the plan, applies stored ingredient exclusions and "
            "rebuilds the full shopping list. "
            f"session_id: {_SESSION_ID_DOC}"
        )
    )
    async def replace_meal(
        session_id: str,
        day_index: int,
        slot: int | None = None,
        goal: DietaryGoal | None = None,
    ) -> MealPlanResponse:
        with tool_call("replace_meal"):
            session = await _require_plan_session(session_id)
            build = deps.meal_plan_service.replace_meal(
                state=session.state, day_index=day_index, slot=slot, goal=goal
            )
            new_state = SessionState(
                session_id=session.state.session_id,
                dietary_goal=session.state.dietary_goal,
                number_of_days=session.state.number_of_days,
                meals_per_day=session.state.meals_per_day,
                meals=build.meals,
                excluded_ingredients=session.state.excluded_ingredients,
                shopping_list=build.shopping_list,
            )
            session.state = new_state
            await deps.session_store.save(session)
            logger.info("session %s: meal replaced (day %d)", session_id, day_index)
            return MealPlanResponse.from_state(new_state)

    @mcp.tool(
        description=(
            "Return the session's current meal plan and shopping list. Use "
            "this when the user asks to see their plan. "
            f"session_id: {_SESSION_ID_DOC}"
        )
    )
    async def get_meal_plan(session_id: str) -> MealPlanResponse:
        with tool_call("get_meal_plan"):
            session = await _require_plan_session(session_id)
            return MealPlanResponse.from_state(session.state)

    @mcp.tool(
        description=(
            "Remember that the user wants to avoid an ingredient (Czech "
            "name preferred). The exclusion applies to all future searches "
            "and replacements; existing meals are NOT changed. The response "
            "lists which current plan days contain the ingredient so you "
            "can offer to replace them. "
            f"session_id: {_SESSION_ID_DOC}"
        )
    )
    async def add_ingredient_exclusion(
        session_id: str, ingredient: str
    ) -> ExclusionResponse:
        with tool_call("add_ingredient_exclusion"):
            key = normalize_ingredient_key(ingredient)
            if not key:
                raise ToolError("ingredient must not be empty")
            session = await deps.session_store.get_or_create(session_id)
            already_excluded = {
                normalize_ingredient_key(i)
                for i in session.state.excluded_ingredients
            }
            if key not in already_excluded:
                session.state.excluded_ingredients.append(ingredient.strip())
                await deps.session_store.save(session)
            affected = deps.meal_plan_service.meals_containing(
                state=session.state, ingredient=ingredient
            )
            logger.info(
                "session %s: excluded %r (affects %d meal(s))",
                session_id,
                ingredient.strip(),
                len(affected),
            )
            return ExclusionResponse(
                excluded_ingredients=session.state.excluded_ingredients,
                affected_days=affected,
            )
