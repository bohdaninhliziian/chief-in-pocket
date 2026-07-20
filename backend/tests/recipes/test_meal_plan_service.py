"""Tests for the deterministic meal-plan workflows (no LLM, no session store)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_enriched, write_enriched_json

from recipes.classification import DietaryGoal
from recipes.exceptions import (
    InvalidDayCount,
    InvalidMealDay,
    InvalidMealSlot,
    InvalidMealsPerDay,
    MealPlanUnavailable,
    NoMealPlan,
    NoReplacementFound,
)
from recipes.repositories.json_recipe_repository import JsonRecipeRepository
from recipes.services import MealPlanService, RecipeService, ShoppingListService
from recipes.sessions import SessionState, day_label_for

HP = DietaryGoal.HIGH_PROTEIN
VEG = DietaryGoal.VEGETARIAN

RECIPES = [
    make_enriched(10, "Guláš", ["Hovězí plec", "Cibule"], goals=[HP]),
    make_enriched(14, "Kuřecí polévka", ["Kuřecí prsa", "Zázvor"], goals=[HP]),
    make_enriched(20, "Losos", ["Losos", "Citron"], goals=[HP]),
    make_enriched(25, "Sýrové rizoto", ["Rýže", "Sýr", "Houby"], goals=[VEG]),
    make_enriched(30, "Čočka", ["Čočka", "Cibule"], goals=[VEG, HP]),
    make_enriched(35, "Krůtí prsa", ["Krůtí prsa", "Rozmarýn"], goals=[HP]),
    make_enriched(40, "Salát s tofu", ["Tofu", "Okurka"], goals=[VEG]),
]


@pytest.fixture
def service(tmp_path: Path) -> MealPlanService:
    repository = JsonRecipeRepository(
        write_enriched_json(tmp_path / "enriched.json", RECIPES)
    )
    return MealPlanService(
        recipe_service=RecipeService(repository),
        shopping_list_service=ShoppingListService(repository),
    )


def state_from_plan(
    service: MealPlanService,
    goal: DietaryGoal = HP,
    days: int = 3,
    meals_per_day: int = 1,
    excluded_ingredients: list[str] | None = None,
) -> SessionState:
    build = service.create_plan(
        goal=goal,
        days=days,
        meals_per_day=meals_per_day,
        excluded_ingredients=excluded_ingredients or [],
    )
    return SessionState(
        session_id="s1",
        dietary_goal=goal,
        number_of_days=days,
        meals_per_day=meals_per_day,
        meals=build.meals,
        excluded_ingredients=excluded_ingredients or [],
        shopping_list=build.shopping_list,
    )


class TestCreatePlan:
    def test_assigns_numbered_days_in_order(self, service: MealPlanService) -> None:
        build = service.create_plan(goal=HP, days=3, meals_per_day=1)
        assert [(m.day_index, m.day_label) for m in build.meals] == [
            (0, "Day 1"),
            (1, "Day 2"),
            (2, "Day 3"),
        ]
        assert [m.recipe_id for m in build.meals] == [10, 14, 20]  # id order

    def test_no_duplicate_recipes(self, service: MealPlanService) -> None:
        build = service.create_plan(goal=HP, days=4, meals_per_day=1)
        ids = [m.recipe_id for m in build.meals]
        assert len(ids) == len(set(ids))

    def test_shopping_list_covers_all_meals(self, service: MealPlanService) -> None:
        build = service.create_plan(goal=HP, days=2, meals_per_day=1)
        ingredients = {item.ingredient for item in build.shopping_list}
        assert ingredients == {"Hovězí plec", "Cibule", "Kuřecí prsa", "Zázvor"}

    def test_fewer_recipes_than_days_is_best_effort(
        self, service: MealPlanService
    ) -> None:
        # 10 days also proves plans are not capped at one week
        build = service.create_plan(goal=VEG, days=10, meals_per_day=1)
        assert build.requested_days == 10
        assert build.planned_days == 3
        assert [m.day_label for m in build.meals] == [
            day_label_for(i) for i in range(3)
        ]

    def test_zero_matches_raises(self, service: MealPlanService) -> None:
        with pytest.raises(MealPlanUnavailable, match="low-carb"):
            service.create_plan(goal=DietaryGoal.LOW_CARB, days=3)

    @pytest.mark.parametrize("days", [0, 32, -1])
    def test_days_out_of_range_rejected(
        self, service: MealPlanService, days: int
    ) -> None:
        with pytest.raises(InvalidDayCount):
            service.create_plan(goal=HP, days=days)

    def test_excluded_ingredients_respected(self, service: MealPlanService) -> None:
        build = service.create_plan(
            goal=HP, days=4, meals_per_day=1, excluded_ingredients=["cibule"]
        )
        assert [m.recipe_id for m in build.meals] == [14, 20, 35]


class TestReplaceMeal:
    @pytest.mark.parametrize("day_index", [0, 1])
    def test_replaces_only_target_day(
        self, service: MealPlanService, day_index: int
    ) -> None:
        state = state_from_plan(service)
        build = service.replace_meal(state=state, day_index=day_index)
        for meal in build.meals:
            original = state.meals[meal.day_index]
            if meal.day_index == day_index:
                assert meal.recipe_id != original.recipe_id
                assert meal.day_label == original.day_label
            else:
                assert meal == original  # preserved byte-for-byte

    def test_replacement_excludes_all_plan_recipes(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service)  # plan: 10, 14, 20
        build = service.replace_meal(state=state, day_index=1)
        ids = [m.recipe_id for m in build.meals]
        assert len(ids) == len(set(ids))
        assert build.meals[1].recipe_id == 30  # next high-protein outside plan

    def test_goal_override_finds_vegetarian(self, service: MealPlanService) -> None:
        state = state_from_plan(service)
        build = service.replace_meal(state=state, day_index=1, goal=VEG)
        replaced = build.meals[1]
        assert replaced.recipe_id == 25  # first vegetarian not already in plan
        assert replaced.goal is VEG

    def test_applies_stored_ingredient_exclusions(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service).model_copy(
            update={"excluded_ingredients": ["houby"]}
        )
        build = service.replace_meal(state=state, day_index=1, goal=VEG)
        # rizoto (25) would be first by id, but contains houby -> skipped
        assert build.meals[1].recipe_id == 30

    def test_shopping_list_rebuilt_for_whole_plan(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service, days=2)
        build = service.replace_meal(state=state, day_index=0)  # 10 -> replacement
        ingredients = {item.ingredient for item in build.shopping_list}
        assert "Hovězí plec" not in ingredients  # removed with recipe 10
        assert "Kuřecí prsa" in ingredients  # kept from recipe 14

    def test_no_plan_raises(self, service: MealPlanService) -> None:
        state = SessionState(session_id="s1")
        with pytest.raises(NoMealPlan):
            service.replace_meal(state=state, day_index=0)

    def test_day_outside_plan_raises(self, service: MealPlanService) -> None:
        state = state_from_plan(service, days=3)
        with pytest.raises(InvalidMealDay, match="planned days"):
            service.replace_meal(state=state, day_index=6)

    def test_no_candidate_raises_and_state_unchanged(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service, goal=VEG, days=3)  # all vegetarians used
        before = state.model_copy(deep=True)
        with pytest.raises(NoReplacementFound, match="vegetarian"):
            service.replace_meal(state=state, day_index=0, goal=VEG)
        assert state == before  # input state never mutated

    def test_input_state_not_mutated_on_success(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service)
        before = state.model_copy(deep=True)
        service.replace_meal(state=state, day_index=1)
        assert state == before


class TestMultipleMealsPerDay:
    def test_fills_days_in_slot_order(self, service: MealPlanService) -> None:
        build = service.create_plan(goal=HP, days=2, meals_per_day=2)
        assert [(m.day_index, m.slot, m.recipe_id) for m in build.meals] == [
            (0, 0, 10),
            (0, 1, 14),
            (1, 0, 20),
            (1, 1, 30),
        ]
        assert build.planned_days == 2

    def test_shortfall_truncates_tail_days(self, service: MealPlanService) -> None:
        # only 5 high-protein recipes exist; 3 days x 3 meals wants 9
        build = service.create_plan(goal=HP, days=3, meals_per_day=3)
        assert len(build.meals) == 5
        assert build.planned_days == 2  # day 1 full, day 2 partial
        assert build.requested_days == 3

    @pytest.mark.parametrize("meals_per_day", [0, 4, -1])
    def test_meals_per_day_out_of_range_rejected(
        self, service: MealPlanService, meals_per_day: int
    ) -> None:
        with pytest.raises(InvalidMealsPerDay):
            service.create_plan(goal=HP, days=2, meals_per_day=meals_per_day)

    def test_replace_specific_slot_preserves_day_siblings(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service, days=2, meals_per_day=2)
        build = service.replace_meal(state=state, day_index=0, slot=1)
        by_key = {(m.day_index, m.slot): m for m in build.meals}
        assert by_key[(0, 0)] == state.meals[0]  # sibling slot untouched
        assert by_key[(0, 1)].recipe_id != state.meals[1].recipe_id
        assert by_key[(1, 0)] == state.meals[2]
        assert by_key[(1, 1)] == state.meals[3]

    def test_slot_required_when_day_has_multiple_meals(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service, days=2, meals_per_day=2)
        with pytest.raises(InvalidMealSlot, match="specify slot"):
            service.replace_meal(state=state, day_index=0)

    def test_unknown_slot_rejected(self, service: MealPlanService) -> None:
        state = state_from_plan(service, days=2, meals_per_day=2)
        with pytest.raises(InvalidMealSlot, match="available slots"):
            service.replace_meal(state=state, day_index=0, slot=2)

    def test_slot_optional_for_single_meal_day(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service, days=3, meals_per_day=1)
        build = service.replace_meal(state=state, day_index=1)
        assert build.meals[1].recipe_id != state.meals[1].recipe_id


class TestMealsContaining:
    def test_reports_affected_day_labels(self, service: MealPlanService) -> None:
        state = state_from_plan(service)  # Day 1=10 (Cibule), Day 2=14, Day 3=20
        assert service.meals_containing(state=state, ingredient=" CIBULE ") == [
            "Day 1"
        ]

    def test_empty_for_unknown_ingredient(self, service: MealPlanService) -> None:
        state = state_from_plan(service)
        assert service.meals_containing(state=state, ingredient="houby") == []

    def test_multi_meal_days_pinpoint_the_slot(
        self, service: MealPlanService
    ) -> None:
        state = state_from_plan(service, days=2, meals_per_day=2)
        # plan: Day 1(10 Cibule, 14), Day 2(20, 30 Cibule)
        assert service.meals_containing(state=state, ingredient="cibule") == [
            "Day 1 (meal 1)",
            "Day 2 (meal 2)",
        ]
