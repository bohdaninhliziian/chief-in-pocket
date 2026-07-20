"""Tests for JsonRecipeRepository and ingredient-key matching."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_enriched, write_enriched_json

from recipes.classification import DietaryGoal, MealType
from recipes.exceptions import RecipeDataError
from recipes.normalization import normalize_ingredient_key
from recipes.repositories import JsonRecipeRepository

GULAS = make_enriched(
    1,
    "Hovězí guláš",
    ["Hovězí plec", "Máslo", "Mléko", "Knedlík"],
    goals=[DietaryGoal.HIGH_PROTEIN],
)
TOFU_SALAD = make_enriched(
    2,
    "Salát s tofu",
    ["Tofu", "Okurka", "Olivový olej"],
    goals=[
        DietaryGoal.VEGETARIAN,
        DietaryGoal.VEGAN,
        DietaryGoal.HIGH_PROTEIN,
        DietaryGoal.LOW_CARB,
        DietaryGoal.GLUTEN_FREE,
        DietaryGoal.DAIRY_FREE,
    ],
    meal_type=MealType.SALAD,
)
SALMON = make_enriched(
    3,
    "Pečený losos",
    ["Losos", "Citron", "Olivový olej"],
    goals=[
        DietaryGoal.PESCATARIAN,
        DietaryGoal.HIGH_PROTEIN,
        DietaryGoal.GLUTEN_FREE,
        DietaryGoal.DAIRY_FREE,
    ],
)
MUSHROOM_SOUP = make_enriched(
    4,
    "Houbová polévka",
    ["Houby", "Smetana", "Brambory"],
    goals=[DietaryGoal.VEGETARIAN],
    meal_type=MealType.SOUP,
)
CAKE = make_enriched(
    5,
    "Bábovka",
    ["Mouka", "Vejce", "Mléko"],
    goals=[DietaryGoal.VEGETARIAN],
    meal_type=MealType.DESSERT,
)

ALL = [GULAS, TOFU_SALAD, SALMON, MUSHROOM_SOUP, CAKE]


@pytest.fixture
def repository(tmp_path: Path) -> JsonRecipeRepository:
    return JsonRecipeRepository(
        write_enriched_json(tmp_path / "enriched.json", ALL)
    )


class TestLoading:
    def test_loads_and_orders_by_id(self, repository: JsonRecipeRepository) -> None:
        assert [r.id for r in repository.all()] == [1, 2, 3, 4, 5]

    def test_missing_file_fails_clearly(self, tmp_path: Path) -> None:
        with pytest.raises(RecipeDataError, match="not found"):
            JsonRecipeRepository(tmp_path / "missing.json")

    def test_invalid_json_fails_clearly(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(RecipeDataError, match="cannot read"):
            JsonRecipeRepository(path)

    def test_invalid_record_fails_clearly(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text('[{"id": "x"}]', encoding="utf-8")
        with pytest.raises(RecipeDataError, match="invalid recipe at index 0"):
            JsonRecipeRepository(path)

    def test_duplicate_ids_rejected(self, tmp_path: Path) -> None:
        path = write_enriched_json(tmp_path / "dup.json", [GULAS, GULAS])
        with pytest.raises(RecipeDataError, match="duplicate recipe id 1"):
            JsonRecipeRepository(path)


class TestGetById:
    def test_returns_recipe(self, repository: JsonRecipeRepository) -> None:
        recipe = repository.get_by_id(1)
        assert recipe is not None and recipe.name == "Hovězí guláš"

    def test_returns_none_for_unknown_id(
        self, repository: JsonRecipeRepository
    ) -> None:
        assert repository.get_by_id(999) is None


class TestSearch:
    @pytest.mark.parametrize(
        ("goal", "expected_ids"),
        [
            (DietaryGoal.HIGH_PROTEIN, [1, 2, 3]),
            (DietaryGoal.VEGETARIAN, [2, 4, 5]),
            (DietaryGoal.VEGAN, [2]),
            (DietaryGoal.PESCATARIAN, [3]),
            (DietaryGoal.GLUTEN_FREE, [2, 3]),
            (DietaryGoal.DAIRY_FREE, [2, 3]),
            (DietaryGoal.LOW_CARB, [2]),
        ],
    )
    def test_search_by_goal(
        self,
        repository: JsonRecipeRepository,
        goal: DietaryGoal,
        expected_ids: list[int],
    ) -> None:
        assert [r.id for r in repository.search(goal=goal, limit=10)] == expected_ids

    def test_meal_type_filter(self, repository: JsonRecipeRepository) -> None:
        soups = repository.search(
            goal=DietaryGoal.VEGETARIAN, limit=10, meal_type=MealType.SOUP
        )
        assert [r.id for r in soups] == [4]

    def test_excluded_ingredients_remove_recipes(
        self, repository: JsonRecipeRepository
    ) -> None:
        results = repository.search(
            goal=DietaryGoal.HIGH_PROTEIN,
            limit=10,
            excluded_ingredients=[" mléko ", "HOUBY"],
        )
        assert [r.id for r in results] == [2, 3]  # guláš (Mléko) dropped

    def test_excluded_recipe_ids_remove_recipes(
        self, repository: JsonRecipeRepository
    ) -> None:
        results = repository.search(
            goal=DietaryGoal.HIGH_PROTEIN,
            limit=10,
            excluded_recipe_ids=[1, 3],
        )
        assert [r.id for r in results] == [2]

    def test_excluded_ids_combine_with_excluded_ingredients(
        self, repository: JsonRecipeRepository
    ) -> None:
        results = repository.search(
            goal=DietaryGoal.HIGH_PROTEIN,
            limit=10,
            excluded_ingredients=["mléko"],
            excluded_recipe_ids=[2],
        )
        assert [r.id for r in results] == [3]

    def test_limit_caps_results(self, repository: JsonRecipeRepository) -> None:
        results = repository.search(goal=DietaryGoal.HIGH_PROTEIN, limit=2)
        assert [r.id for r in results] == [1, 2]

    def test_zero_matches_returns_empty_list(
        self, repository: JsonRecipeRepository
    ) -> None:
        assert (
            repository.search(
                goal=DietaryGoal.VEGAN, limit=5, meal_type=MealType.SOUP
            )
            == []
        )


class TestIngredientMatching:
    def test_whitespace_normalized(self) -> None:
        assert normalize_ingredient_key(" mléko ") == normalize_ingredient_key("mléko")
        assert normalize_ingredient_key("hovězí  plec") == normalize_ingredient_key(
            "hovězí plec"
        )

    def test_case_insensitive(self) -> None:
        assert normalize_ingredient_key("MLÉKO") == normalize_ingredient_key("mléko")

    def test_czech_characters_and_unicode_forms(self) -> None:
        composed = "Mléko"  # é as single code point
        decomposed = "Mléko"  # e + combining acute accent
        assert normalize_ingredient_key(composed) == normalize_ingredient_key(
            decomposed
        )
        assert normalize_ingredient_key("Žluťoučký kůň") == "žluťoučký kůň"
