"""MCP tool definitions — thin wrappers around the runtime services.

Each tool only: validates input (via type annotations + the services),
calls one service method, maps the result to an MCP response model, and
translates domain exceptions into MCP-friendly ToolErrors (no tracebacks).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from recipes.classification import DietaryGoal, MealType
from recipes.exceptions import ChefInMyPocketError
from recipes.mcp_server.dependencies import Dependencies
from recipes.mcp_server.models import RecipeDetail, RecipeSummary, ShoppingListEntry
from recipes.services.recipe_service import MAX_RECIPES

logger = logging.getLogger(__name__)


@contextmanager
def tool_call(tool_name: str) -> Iterator[None]:
    """Log tool name + execution time; translate domain errors for MCP.

    Shared by every tool module of this server."""
    started = time.perf_counter()
    try:
        yield
    except ChefInMyPocketError as exc:
        logger.warning("tool %s rejected: %s", tool_name, exc)
        raise ToolError(str(exc)) from None
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info("tool %s finished in %.1f ms", tool_name, elapsed_ms)


def register_tools(mcp: FastMCP, deps: Dependencies) -> None:
    @mcp.tool(
        description=(
            "List the dietary goals this recipe collection supports. Call "
            "this to discover valid values for the 'goal' parameter of "
            "search_recipes."
        )
    )
    def list_supported_goals() -> list[str]:
        with tool_call("list_supported_goals"):
            return [goal.value for goal in DietaryGoal]

    @mcp.tool(
        description=(
            "Search recipes matching a supported dietary goal. Returns up "
            f"to {MAX_RECIPES} lightweight recipe summaries (id, name, "
            "goals, meal type) — use get_recipe for full details. "
            "Optionally exclude recipes containing specific ingredients "
            "(Czech names, case-insensitive) or restrict to one meal type. "
            "An empty list means no recipe matched."
        )
    )
    def search_recipes(
        goal: DietaryGoal,
        count: int,
        excluded_ingredients: list[str] | None = None,
        meal_type: MealType | None = None,
    ) -> list[RecipeSummary]:
        with tool_call("search_recipes"):
            recipes = deps.recipe_service.search_recipes(
                goal=goal,
                count=count,
                excluded_ingredients=excluded_ingredients or [],
                meal_type=meal_type,
            )
            return [RecipeSummary.from_recipe(recipe) for recipe in recipes]

    @mcp.tool(
        description=(
            "Get one recipe in full detail: name, description, ingredients, "
            "step-by-step instructions, supported dietary goals, allergens "
            "and meal type."
        )
    )
    def get_recipe(recipe_id: int) -> RecipeDetail:
        with tool_call("get_recipe"):
            return RecipeDetail.from_recipe(
                deps.recipe_service.get_recipe(recipe_id)
            )

    @mcp.tool(
        description=(
            "Build a merged shopping list for the given recipe ids. "
            "Duplicate ingredients across recipes are merged; each entry "
            "lists which recipes need it. The dataset has no quantities, "
            "so only ingredient names are returned."
        )
    )
    def build_shopping_list(recipe_ids: list[int]) -> list[ShoppingListEntry]:
        with tool_call("build_shopping_list"):
            items = deps.shopping_list_service.build(recipe_ids)
            return [
                ShoppingListEntry(ingredient=item.ingredient, recipes=item.recipes)
                for item in items
            ]
