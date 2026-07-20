"""Structured session state for the conversational agent.

This is the canonical business truth for a chat session: message history
answers "what has been discussed?", these models answer "what is currently
true?". The meal plan is never reconstructed from chat text.

Pure Pydantic — no pydantic_ai imports, so the MCP server can depend on
this module without pulling in the agent stack.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from recipes.classification import DietaryGoal

MIN_PLAN_DAYS = 1
# Plans are generic day sequences ("Day 1".."Day N"), deliberately not
# tied to calendar weekdays. The cap is a sanity bound, not a week:
# recipes never repeat within a plan, so the dataset must cover
# days x meals_per_day unique recipes.
MAX_PLAN_DAYS = 31
MIN_MEALS_PER_DAY = 1
MAX_MEALS_PER_DAY = 3


def day_label_for(day_index: int) -> str:
    """Canonical human label for a 0-based plan day. Python owns this;
    the LLM never invents day labels or indexes."""
    return f"Day {day_index + 1}"


class MealAssignment(BaseModel):
    day_index: int = Field(ge=0, le=MAX_PLAN_DAYS - 1)
    day_label: str
    # Slot within the day (0-based). Slots carry no fixed breakfast/lunch
    # semantics — they are simply "meal 1..N of the day".
    slot: int = Field(default=0, ge=0, le=MAX_MEALS_PER_DAY - 1)
    recipe_id: int
    recipe_name: str
    goal: DietaryGoal

    @model_validator(mode="after")
    def _label_matches_index(self) -> MealAssignment:
        expected = day_label_for(self.day_index)
        if self.day_label != expected:
            raise ValueError(
                f"day_label {self.day_label!r} does not match day_index "
                f"{self.day_index} (expected {expected!r})"
            )
        return self


class SessionShoppingItem(BaseModel):
    ingredient: str
    recipes: list[int]


class SessionState(BaseModel):
    """Validated canonical state; the model itself refuses corrupt plans."""

    session_id: str
    dietary_goal: DietaryGoal | None = None
    number_of_days: int | None = Field(default=None, ge=MIN_PLAN_DAYS, le=MAX_PLAN_DAYS)
    meals_per_day: int | None = Field(
        default=None, ge=MIN_MEALS_PER_DAY, le=MAX_MEALS_PER_DAY
    )
    meals: list[MealAssignment] = Field(default_factory=list)
    excluded_ingredients: list[str] = Field(default_factory=list)
    shopping_list: list[SessionShoppingItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _meals_consistent(self) -> SessionState:
        slots = [(meal.day_index, meal.slot) for meal in self.meals]
        if len(slots) != len(set(slots)):
            raise ValueError(
                "meal plan assigns more than one recipe to the same day and slot"
            )
        recipe_ids = [meal.recipe_id for meal in self.meals]
        if len(recipe_ids) != len(set(recipe_ids)):
            raise ValueError("meal plan contains duplicate recipes")
        self.meals.sort(key=lambda meal: (meal.day_index, meal.slot))
        return self
