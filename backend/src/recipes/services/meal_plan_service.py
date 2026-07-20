"""Deterministic meal-plan workflows for the conversational agent.

Pure functions of their inputs: nothing here reads or writes session
storage, so a failed operation can never corrupt stored state — callers
persist the returned build only after it validated. No LLM, no I/O beyond
the injected repository-backed services.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import BaseModel

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
from recipes.normalization import normalize_ingredient_key
from recipes.services.recipe_service import RecipeService
from recipes.services.shopping_list_service import ShoppingListItem, ShoppingListService
from recipes.sessions.models import (
    MAX_MEALS_PER_DAY,
    MAX_PLAN_DAYS,
    MIN_MEALS_PER_DAY,
    MIN_PLAN_DAYS,
    MealAssignment,
    SessionShoppingItem,
    SessionState,
    day_label_for,
)

DEFAULT_MEALS_PER_DAY = 3

logger = logging.getLogger(__name__)


class MealPlanBuild(BaseModel):
    """Result of a plan operation; callers merge it into session state."""

    meals: list[MealAssignment]
    shopping_list: list[SessionShoppingItem]
    requested_days: int
    planned_days: int


class MealPlanService:
    """Owns plan creation and replacement; days are a generic 0-based
    sequence labelled "Day 1".."Day N", not calendar weekdays."""

    def __init__(
        self,
        *,
        recipe_service: RecipeService,
        shopping_list_service: ShoppingListService,
    ) -> None:
        self._recipes = recipe_service
        self._shopping = shopping_list_service

    def create_plan(
        self,
        *,
        goal: DietaryGoal,
        days: int,
        meals_per_day: int = DEFAULT_MEALS_PER_DAY,
        excluded_ingredients: Sequence[str] = (),
    ) -> MealPlanBuild:
        """Build a plan of ``days`` x ``meals_per_day`` meals (best effort).

        Recipes fill the plan day by day (day 1's meals first), so a
        shortfall truncates the tail of the plan. Raises
        :class:`InvalidDayCount` outside 1..MAX_PLAN_DAYS,
        :class:`InvalidMealsPerDay` outside 1..3 and
        :class:`MealPlanUnavailable` when nothing matches.
        """
        if not isinstance(days, int) or isinstance(days, bool):
            raise InvalidDayCount(f"days must be an integer, got {days!r}")
        if not MIN_PLAN_DAYS <= days <= MAX_PLAN_DAYS:
            raise InvalidDayCount(
                f"days must be between {MIN_PLAN_DAYS} and {MAX_PLAN_DAYS}, got {days}"
            )
        if not isinstance(meals_per_day, int) or isinstance(meals_per_day, bool):
            raise InvalidMealsPerDay(
                f"meals_per_day must be an integer, got {meals_per_day!r}"
            )
        if not MIN_MEALS_PER_DAY <= meals_per_day <= MAX_MEALS_PER_DAY:
            raise InvalidMealsPerDay(
                f"meals_per_day must be between {MIN_MEALS_PER_DAY} and "
                f"{MAX_MEALS_PER_DAY}, got {meals_per_day}"
            )

        recipes = self._recipes.search_recipes(
            goal=goal,
            count=days * meals_per_day,
            excluded_ingredients=excluded_ingredients,
        )
        if not recipes:
            raise MealPlanUnavailable(
                f"no {goal.value} recipes match the current criteria"
            )

        meals = [
            MealAssignment(
                day_index=index // meals_per_day,
                day_label=day_label_for(index // meals_per_day),
                slot=index % meals_per_day,
                recipe_id=recipe.id,
                recipe_name=recipe.name,
                goal=goal,
            )
            for index, recipe in enumerate(recipes)
        ]
        build = MealPlanBuild(
            meals=meals,
            shopping_list=self._build_shopping_list(meals),
            requested_days=days,
            planned_days=len({meal.day_index for meal in meals}),
        )
        logger.info(
            "created plan goal=%s requested=%dx%d planned=%d meal(s) over %d day(s)",
            goal.value,
            days,
            meals_per_day,
            len(meals),
            build.planned_days,
        )
        return build

    def replace_meal(
        self,
        *,
        state: SessionState,
        day_index: int,
        slot: int | None = None,
        goal: DietaryGoal | None = None,
    ) -> MealPlanBuild:
        """Replace exactly one meal, preserving every other meal.

        ``slot`` may be omitted only when the day has a single meal; with
        several meals it is required so the wrong one is never replaced.
        The replacement search excludes every recipe already in the plan and
        applies the session's stored ingredient exclusions. Raises
        :class:`NoMealPlan`, :class:`InvalidMealDay`, :class:`InvalidMealSlot`
        or :class:`NoReplacementFound`; the input ``state`` is never mutated.
        """
        if not state.meals:
            raise NoMealPlan("no meal plan exists yet; create one first")
        day_meals = [meal for meal in state.meals if meal.day_index == day_index]
        if not day_meals:
            planned = ", ".join(
                f"{label} ({index})"
                for index, label in sorted(
                    {(m.day_index, m.day_label) for m in state.meals}
                )
            )
            raise InvalidMealDay(
                f"day_index {day_index} is not part of the current plan; "
                f"planned days: {planned}"
            )
        if slot is not None and (not isinstance(slot, int) or isinstance(slot, bool)):
            raise InvalidMealSlot(f"slot must be an integer, got {slot!r}")
        if slot is None:
            if len(day_meals) > 1:
                # Slots are contiguous 0..n-1: day-major fill creates them
                # that way and replacement preserves the slot, so the range
                # in this message always matches reality.
                raise InvalidMealSlot(
                    f"{day_meals[0].day_label} has {len(day_meals)} meals; "
                    f"specify slot 0..{len(day_meals) - 1} to pick one"
                )
            target = day_meals[0]
        else:
            found = next((meal for meal in day_meals if meal.slot == slot), None)
            if found is None:
                available = ", ".join(str(meal.slot) for meal in day_meals)
                raise InvalidMealSlot(
                    f"slot {slot} is not part of {day_meals[0].day_label}; "
                    f"available slots: {available}"
                )
            target = found
        target_goal = goal or state.dietary_goal
        if target_goal is None:
            raise NoMealPlan("the session has no dietary goal; create a plan first")

        candidates = self._recipes.search_recipes(
            goal=target_goal,
            count=1,
            excluded_ingredients=state.excluded_ingredients,
            excluded_recipe_ids=[meal.recipe_id for meal in state.meals],
        )
        if not candidates:
            raise NoReplacementFound(
                f"no {target_goal.value} recipe is available outside the current plan"
            )
        replacement = candidates[0]

        meals = [
            meal
            if (meal.day_index, meal.slot) != (target.day_index, target.slot)
            else MealAssignment(
                day_index=target.day_index,
                day_label=target.day_label,
                slot=target.slot,
                recipe_id=replacement.id,
                recipe_name=replacement.name,
                goal=target_goal,
            )
            for meal in state.meals
        ]
        build = MealPlanBuild(
            meals=meals,
            shopping_list=self._build_shopping_list(meals),
            requested_days=state.number_of_days or len({m.day_index for m in meals}),
            planned_days=len({m.day_index for m in meals}),
        )
        logger.info(
            "replaced meal day=%d slot=%d recipe %d -> %d (goal=%s)",
            target.day_index,
            target.slot,
            target.recipe_id,
            replacement.id,
            target_goal.value,
        )
        return build

    def meals_containing(self, *, state: SessionState, ingredient: str) -> list[str]:
        """Human labels of current meals whose recipe contains ``ingredient``.

        On multi-meal days the label pinpoints the slot ("Day 1 (meal 2)")
        so the agent can offer a precise replacement.
        """
        key = normalize_ingredient_key(ingredient)
        if not key:
            return []
        day_meal_counts: dict[int, int] = {}
        for meal in state.meals:
            day_meal_counts[meal.day_index] = day_meal_counts.get(meal.day_index, 0) + 1
        affected: list[str] = []
        for meal in state.meals:
            recipe = self._recipes.get_recipe(meal.recipe_id)
            if key in {normalize_ingredient_key(i) for i in recipe.ingredients}:
                label = (
                    meal.day_label
                    if day_meal_counts[meal.day_index] == 1
                    else f"{meal.day_label} (meal {meal.slot + 1})"
                )
                affected.append(label)
        return affected

    def _build_shopping_list(
        self, meals: Sequence[MealAssignment]
    ) -> list[SessionShoppingItem]:
        items: list[ShoppingListItem] = self._shopping.build(
            [meal.recipe_id for meal in meals]
        )
        return [
            SessionShoppingItem(ingredient=item.ingredient, recipes=item.recipes)
            for item in items
        ]
