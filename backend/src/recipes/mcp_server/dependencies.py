"""Shared dependencies for the MCP server.

The repository and services are constructed exactly once at server startup
and injected into every tool — tools never instantiate them.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from recipes.repositories import JsonRecipeRepository
from recipes.services import MealPlanService, RecipeService, ShoppingListService
from recipes.sessions import InMemorySessionStore, SessionStore

logger = logging.getLogger(__name__)

DATA_PATH_ENV_VAR = "RECIPES_ENRICHED_PATH"
DEFAULT_DATA_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "processed" / "recipes_enriched.json"
)


@dataclass(frozen=True)
class Dependencies:
    repository: JsonRecipeRepository
    recipe_service: RecipeService
    shopping_list_service: ShoppingListService
    meal_plan_service: MealPlanService
    session_store: SessionStore


def build_dependencies(
    data_path: Path | None = None,
    *,
    session_store: SessionStore | None = None,
) -> Dependencies:
    """Wire repository → services once. Path resolution order:
    explicit argument > $RECIPES_ENRICHED_PATH > default processed file.

    ``session_store`` defaults to a fresh in-memory store; the chat API
    passes its own so the HTTP layer and the MCP tools share sessions.
    """
    path = data_path or Path(
        os.environ.get(DATA_PATH_ENV_VAR, str(DEFAULT_DATA_PATH))
    )
    repository = JsonRecipeRepository(path)
    recipe_service = RecipeService(repository=repository)
    shopping_list_service = ShoppingListService(repository=repository)
    logger.info("MCP dependencies ready (%d recipes)", len(repository.all()))
    return Dependencies(
        repository=repository,
        recipe_service=recipe_service,
        shopping_list_service=shopping_list_service,
        meal_plan_service=MealPlanService(
            recipe_service=recipe_service,
            shopping_list_service=shopping_list_service,
        ),
        session_store=session_store or InMemorySessionStore(),
    )
