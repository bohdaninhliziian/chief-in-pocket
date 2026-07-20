"""Domain exceptions for the runtime recipe layer.

Exceptions are reserved for programming and validation errors. A search
that matches nothing returns an empty list — that is a normal outcome,
never an exception.
"""

from __future__ import annotations


class ChefInMyPocketError(Exception):
    """Base class for all domain errors."""


class RecipeDataError(ChefInMyPocketError):
    """The recipe data source is missing, unreadable or invalid."""


class RecipeNotFound(ChefInMyPocketError):
    """A recipe id was requested that does not exist."""

    def __init__(self, recipe_id: int) -> None:
        self.recipe_id = recipe_id
        super().__init__(f"recipe {recipe_id} does not exist")


class InvalidDietaryGoal(ChefInMyPocketError):
    """The requested dietary goal is not one of the supported values."""


class InvalidMealType(ChefInMyPocketError):
    """The requested meal type is not one of the supported values."""


class InvalidRecipeCount(ChefInMyPocketError):
    """The requested recipe count is outside the allowed range."""


class InvalidDayCount(ChefInMyPocketError):
    """The requested number of plan days is outside 1..MAX_PLAN_DAYS."""


class MealPlanUnavailable(ChefInMyPocketError):
    """No recipe matches the requested plan criteria at all."""


class NoMealPlan(ChefInMyPocketError):
    """A follow-up operation was requested before any meal plan exists."""


class InvalidMealDay(ChefInMyPocketError):
    """The referenced day is not part of the current meal plan."""


class InvalidMealsPerDay(ChefInMyPocketError):
    """The requested number of meals per day is outside 1..3."""


class InvalidMealSlot(ChefInMyPocketError):
    """The referenced meal slot is missing, ambiguous or not in the plan."""


class NoReplacementFound(ChefInMyPocketError):
    """No candidate recipe exists outside the current plan and exclusions."""
