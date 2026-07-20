"""Tests for RecipeService input validation and business rules."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from conftest import make_enriched

from recipes.classification import DietaryGoal, EnrichedRecipe, MealType
from recipes.exceptions import (
    InvalidDietaryGoal,
    InvalidMealType,
    InvalidRecipeCount,
    RecipeNotFound,
)
from recipes.services import RecipeService
from recipes.services.recipe_service import MAX_RECIPES


class InMemoryRepository:
    """Minimal RecipeRepository implementation, proving swappability."""

    def __init__(self, recipes: list[EnrichedRecipe]) -> None:
        self._recipes = sorted(recipes, key=lambda r: r.id)

    def get_by_id(self, recipe_id: int) -> EnrichedRecipe | None:
        return next((r for r in self._recipes if r.id == recipe_id), None)

    def search(
        self,
        *,
        goal: DietaryGoal,
        limit: int,
        excluded_ingredients: Sequence[str] = (),
        excluded_recipe_ids: Sequence[int] = (),
        meal_type: MealType | None = None,
    ) -> list[EnrichedRecipe]:
        excluded_ids = set(excluded_recipe_ids)
        matches = [
            r
            for r in self._recipes
            if r.id not in excluded_ids
            and goal in r.supported_goals
            and (meal_type is None or r.meal_type is meal_type)
        ]
        return matches[:limit]

    def all(self) -> list[EnrichedRecipe]:
        return list(self._recipes)


RECIPES = [
    make_enriched(1, "Guláš", ["Hovězí plec"], goals=[DietaryGoal.HIGH_PROTEIN]),
    make_enriched(2, "Losos", ["Losos"], goals=[DietaryGoal.HIGH_PROTEIN]),
]


@pytest.fixture
def service() -> RecipeService:
    return RecipeService(repository=InMemoryRepository(RECIPES))


class TestValidation:
    @pytest.mark.parametrize("count", [0, -1, MAX_RECIPES + 1, 500])
    def test_count_out_of_range_rejected(
        self, service: RecipeService, count: int
    ) -> None:
        with pytest.raises(InvalidRecipeCount, match=f"between 1 and {MAX_RECIPES}"):
            service.search_recipes(goal=DietaryGoal.HIGH_PROTEIN, count=count)

    @pytest.mark.parametrize("count", ["3", 2.5, None, True])
    def test_non_integer_count_rejected(
        self, service: RecipeService, count: object
    ) -> None:
        with pytest.raises(InvalidRecipeCount):
            service.search_recipes(goal=DietaryGoal.HIGH_PROTEIN, count=count)  # type: ignore[arg-type]

    @pytest.mark.parametrize("goal", ["keto", "paleo", "low-fat", ""])
    def test_unsupported_goal_rejected(self, service: RecipeService, goal: str) -> None:
        with pytest.raises(InvalidDietaryGoal, match="unsupported dietary goal"):
            service.search_recipes(goal=goal, count=3)

    def test_goal_accepted_as_string_value(self, service: RecipeService) -> None:
        results = service.search_recipes(goal="high-protein", count=2)
        assert [r.id for r in results] == [1, 2]

    def test_unsupported_meal_type_rejected(self, service: RecipeService) -> None:
        with pytest.raises(InvalidMealType):
            service.search_recipes(
                goal=DietaryGoal.HIGH_PROTEIN, count=2, meal_type="brunch"
            )


class TestBusinessRules:
    def test_zero_matches_returns_empty_list(self, service: RecipeService) -> None:
        assert service.search_recipes(goal=DietaryGoal.VEGAN, count=3) == []

    def test_fewer_matches_than_requested_returns_all(
        self, service: RecipeService
    ) -> None:
        results = service.search_recipes(goal=DietaryGoal.HIGH_PROTEIN, count=7)
        assert [r.id for r in results] == [1, 2]

    def test_count_limits_results(self, service: RecipeService) -> None:
        results = service.search_recipes(goal=DietaryGoal.HIGH_PROTEIN, count=1)
        assert [r.id for r in results] == [1]

    def test_excluded_recipe_ids_passed_through(self, service: RecipeService) -> None:
        results = service.search_recipes(
            goal=DietaryGoal.HIGH_PROTEIN, count=7, excluded_recipe_ids=[1]
        )
        assert [r.id for r in results] == [2]

    def test_get_recipe_raises_for_unknown_id(self, service: RecipeService) -> None:
        with pytest.raises(RecipeNotFound):
            service.get_recipe(999)
