"""Recipe search service: input validation on top of the repository."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from recipes.classification import DietaryGoal, EnrichedRecipe, MealType
from recipes.exceptions import (
    InvalidDietaryGoal,
    InvalidMealType,
    InvalidRecipeCount,
    RecipeNotFound,
)
from recipes.repositories.recipe_repository import RecipeRepository
from recipes.sessions.models import MAX_MEALS_PER_DAY, MAX_PLAN_DAYS

logger = logging.getLogger(__name__)

# Upper bound for one search: the longest plan at the most meals per day.
MAX_RECIPES = MAX_PLAN_DAYS * MAX_MEALS_PER_DAY


class RecipeService:
    """Validates requests and delegates deterministic search to a repository.

    The repository is injected, never instantiated here, so any
    ``RecipeRepository`` implementation (JSON today, PostgreSQL later)
    works unchanged.
    """

    def __init__(self, repository: RecipeRepository) -> None:
        self._repository = repository

    def search_recipes(
        self,
        *,
        goal: DietaryGoal | str,
        count: int,
        excluded_ingredients: Sequence[str] = (),
        excluded_recipe_ids: Sequence[int] = (),
        meal_type: MealType | str | None = None,
    ) -> list[EnrichedRecipe]:
        """Return up to ``count`` recipes supporting ``goal``.

        Fewer matches than requested (including zero) is a normal outcome
        and returns whatever matched — never an exception.
        """
        goal = self._parse_goal(goal)
        meal_type = self._parse_meal_type(meal_type)
        if not isinstance(count, int) or isinstance(count, bool):
            raise InvalidRecipeCount(f"count must be an integer, got {count!r}")
        if not 1 <= count <= MAX_RECIPES:
            raise InvalidRecipeCount(
                f"count must be between 1 and {MAX_RECIPES}, got {count}"
            )

        recipes = self._repository.search(
            goal=goal,
            limit=count,
            excluded_ingredients=list(excluded_ingredients),
            excluded_recipe_ids=list(excluded_recipe_ids),
            meal_type=meal_type,
        )
        logger.info(
            "search goal=%s count=%d excluded=%d excluded_ids=%d meal_type=%s -> %d recipe(s)",
            goal.value,
            count,
            len(excluded_ingredients),
            len(excluded_recipe_ids),
            meal_type.value if meal_type else None,
            len(recipes),
        )
        return recipes

    def get_recipe(self, recipe_id: int) -> EnrichedRecipe:
        recipe = self._repository.get_by_id(recipe_id)
        if recipe is None:
            raise RecipeNotFound(recipe_id)
        return recipe

    @staticmethod
    def _parse_goal(goal: DietaryGoal | str) -> DietaryGoal:
        if isinstance(goal, DietaryGoal):
            return goal
        try:
            return DietaryGoal(goal)
        except ValueError:
            supported = ", ".join(g.value for g in DietaryGoal)
            raise InvalidDietaryGoal(
                f"unsupported dietary goal {goal!r}; supported goals: {supported}"
            ) from None

    @staticmethod
    def _parse_meal_type(meal_type: MealType | str | None) -> MealType | None:
        if meal_type is None or isinstance(meal_type, MealType):
            return meal_type
        try:
            return MealType(meal_type)
        except ValueError:
            supported = ", ".join(m.value for m in MealType)
            raise InvalidMealType(
                f"unsupported meal type {meal_type!r}; supported: {supported}"
            ) from None
