"""Repository abstraction for enriched recipes.

Services depend only on this protocol (injected through their constructor),
so ``JsonRecipeRepository`` can later be swapped for a
``PostgresRecipeRepository`` without touching the services, the MCP server
or the agent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from recipes.classification import DietaryGoal, EnrichedRecipe, MealType


class RecipeRepository(Protocol):
    def get_by_id(self, recipe_id: int) -> EnrichedRecipe | None:
        """Return the recipe with this id, or None when it does not exist."""
        ...

    def search(
        self,
        *,
        goal: DietaryGoal,
        limit: int,
        excluded_ingredients: Sequence[str] = (),
        excluded_recipe_ids: Sequence[int] = (),
        meal_type: MealType | None = None,
    ) -> list[EnrichedRecipe]:
        """Deterministically filter recipes; never raises on zero matches."""
        ...

    def all(self) -> list[EnrichedRecipe]:
        """Return every recipe."""
        ...
