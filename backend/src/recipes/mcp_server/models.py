"""Dedicated MCP response models.

Internal repository models (EnrichedRecipe with provenance, evidence,
fingerprints) are never exposed over MCP — tools return these lean,
purpose-built shapes instead.
"""

from __future__ import annotations

from pydantic import BaseModel

from recipes.classification import Allergen, DietaryGoal, EnrichedRecipe, MealType
from recipes.sessions import MealAssignment, SessionState


class RecipeSummary(BaseModel):
    """Lightweight search result — no instructions or description."""

    id: int
    name: str
    supported_goals: list[DietaryGoal]
    meal_type: MealType | None

    @classmethod
    def from_recipe(cls, recipe: EnrichedRecipe) -> RecipeSummary:
        return cls(
            id=recipe.id,
            name=recipe.name,
            supported_goals=recipe.supported_goals,
            meal_type=recipe.meal_type,
        )


class RecipeDetail(BaseModel):
    """Full recipe view returned by get_recipe."""

    id: int
    name: str
    author: str | None
    description: str
    ingredients: list[str]
    instructions: list[str]
    supported_goals: list[DietaryGoal]
    allergens: list[Allergen]
    meal_type: MealType | None

    @classmethod
    def from_recipe(cls, recipe: EnrichedRecipe) -> RecipeDetail:
        return cls(
            id=recipe.id,
            name=recipe.name,
            author=recipe.author,
            description=recipe.description,
            ingredients=recipe.ingredients,
            instructions=recipe.instructions,
            supported_goals=recipe.supported_goals,
            allergens=recipe.allergens,
            meal_type=recipe.meal_type,
        )


class ShoppingListEntry(BaseModel):
    """One merged ingredient with the recipe ids that require it."""

    ingredient: str
    recipes: list[int]


class MealPlanResponse(BaseModel):
    """Current meal plan of a chat session, as returned by business tools.

    ``planned_meals`` < ``requested_meals`` signals a best-effort plan
    built from fewer matching recipes than the user asked for — this
    catches partial days that ``planned_days`` alone would miss (e.g. a
    full day 1 but only two of three day 2 meals).
    """

    session_id: str
    dietary_goal: DietaryGoal | None
    requested_days: int | None
    meals_per_day: int | None
    planned_days: int
    requested_meals: int | None
    planned_meals: int
    meals: list[MealAssignment]
    excluded_ingredients: list[str]
    shopping_list: list[ShoppingListEntry]

    @classmethod
    def from_state(cls, state: SessionState) -> MealPlanResponse:
        requested_meals = (
            state.number_of_days * state.meals_per_day
            if state.number_of_days is not None and state.meals_per_day is not None
            else None
        )
        return cls(
            session_id=state.session_id,
            dietary_goal=state.dietary_goal,
            requested_days=state.number_of_days,
            meals_per_day=state.meals_per_day,
            planned_days=len({meal.day_index for meal in state.meals}),
            requested_meals=requested_meals,
            planned_meals=len(state.meals),
            meals=state.meals,
            excluded_ingredients=state.excluded_ingredients,
            shopping_list=[
                ShoppingListEntry(ingredient=item.ingredient, recipes=item.recipes)
                for item in state.shopping_list
            ],
        )


class ExclusionResponse(BaseModel):
    """Result of adding an ingredient exclusion (store-only, no auto-replace)."""

    excluded_ingredients: list[str]
    affected_days: list[str]
