"""Shared fixtures for classification and enrichment tests.

No test in this suite ever calls a real model provider:
``ALLOW_MODEL_REQUESTS`` is disabled globally.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai import models

from recipes.classification import (
    Allergen,
    ClassificationEvidence,
    ClassificationMetadata,
    DietaryGoal,
    EnrichedRecipe,
    GoalEvidence,
    MealType,
    RecipeClassification,
    ReviewStatus,
)
from recipes.models import Recipe

models.ALLOW_MODEL_REQUESTS = False


def make_recipe(
    recipe_id: int = 1,
    name: str = "Testovací recept",
    ingredients: list[str] | None = None,
    **kwargs: object,
) -> Recipe:
    return Recipe(
        id=recipe_id,
        name=name,
        author=kwargs.get("author", "Roman Vaněk"),
        description=kwargs.get("description", "Popis receptu."),
        ingredients=ingredients or ["Sůl"],
        instructions=kwargs.get("instructions", ["Vše smícháme a podáváme."]),
    )


def make_classification(
    goals: list[DietaryGoal] | None = None,
    allergens: list[Allergen] | None = None,
    meal_type: MealType = MealType.MAIN_COURSE,
    confidence: float = 0.9,
) -> RecipeClassification:
    goals = goals or []
    return RecipeClassification(
        supported_goals=goals,
        allergens=allergens or [],
        meal_type=meal_type,
        confidence=confidence,
        goal_evidence=[
            GoalEvidence(goal=goal, reason=f"reason for {goal.value}")
            for goal in goals
        ],
        allergen_reason="Detected from ingredients.",
        meal_type_reason="Inferred from the recipe name.",
    )


def make_enriched(
    recipe_id: int,
    name: str,
    ingredients: list[str],
    goals: list[DietaryGoal] | None = None,
    meal_type: MealType = MealType.MAIN_COURSE,
    allergens: list[Allergen] | None = None,
) -> EnrichedRecipe:
    return EnrichedRecipe(
        id=recipe_id,
        name=name,
        author="Roman Vaněk",
        description="Popis receptu.",
        ingredients=ingredients,
        instructions=["Vše smícháme a podáváme."],
        supported_goals=goals or [],
        allergens=allergens or [],
        meal_type=meal_type,
        classification=ClassificationMetadata(
            source="llm",
            model="test-model",
            classifier_version="1",
            prompt_version="1",
            classified_at=datetime(2026, 1, 1, tzinfo=UTC),
            confidence=0.9,
            review_status=ReviewStatus.ACCEPTED,
            fingerprint=f"fp-{recipe_id}",
        ),
        classification_evidence=ClassificationEvidence(),
    )


def write_enriched_json(path: Path, recipes: list[EnrichedRecipe]) -> Path:
    path.write_text(
        json.dumps(
            [recipe.model_dump(mode="json") for recipe in recipes],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


# Representative fixture recipes (Czech, mirroring the real dataset).
GULAS = make_recipe(
    10,
    "Hovězí guláš s karlovarským knedlíkem",
    ["Hovězí plec vcelku", "Karlovarský knedlík", "Mléko", "Vejce", "Cibule", "Sádlo"],
)
NUT_CAKE = make_recipe(
    11,
    "Bábovka s ořechy",
    ["Hrubá mouka", "Vlašské ořechy", "Vejce", "Máslo", "Cukr krupice", "Mléko"],
)
FISH_RECIPE = make_recipe(
    30,
    "Pečený losos s koprem",
    ["Losos", "Citron", "Olivový olej", "Kopr", "Sůl"],
)


class FakeClassifier:
    """Protocol-conforming classifier with scripted per-recipe responses."""

    def __init__(
        self,
        responses: dict[int, RecipeClassification | Exception] | None = None,
        default: RecipeClassification | None = None,
    ) -> None:
        self.responses = responses or {}
        self.default = default or make_classification()
        self.calls: list[int] = []

    async def classify(self, recipe: Recipe) -> RecipeClassification:
        self.calls.append(recipe.id)
        response = self.responses.get(recipe.id, self.default)
        if isinstance(response, Exception):
            raise response
        return response
