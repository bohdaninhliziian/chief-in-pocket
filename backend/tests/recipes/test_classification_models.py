"""Tests for the structured-output schema (enums, dedup, normalization)."""

from __future__ import annotations

import pytest
from conftest import make_classification
from pydantic import ValidationError

from recipes.classification import (
    Allergen,
    DietaryGoal,
    MealType,
    RecipeClassification,
)

VALID_PAYLOAD = {
    "supported_goals": ["high-protein"],
    "allergens": ["gluten", "milk", "eggs"],
    "meal_type": "main-course",
    "confidence": 0.94,
    "goal_evidence": [
        {"goal": "high-protein", "reason": "Beef is the primary protein."}
    ],
    "allergen_reason": "Contains dumplings, milk and eggs.",
    "meal_type_reason": "Guláš with dumplings is a main course.",
}


def test_valid_structured_output_becomes_classification() -> None:
    classification = RecipeClassification.model_validate(VALID_PAYLOAD)
    assert classification.supported_goals == [DietaryGoal.HIGH_PROTEIN]
    assert classification.allergens == [
        Allergen.GLUTEN,
        Allergen.MILK,
        Allergen.EGGS,
    ]
    assert classification.meal_type is MealType.MAIN_COURSE
    assert classification.confidence == 0.94


@pytest.mark.parametrize("goal", ["keto", "paleo", "Mediterranean", "low-fat"])
def test_unsupported_dietary_goals_rejected(goal: str) -> None:
    payload = {**VALID_PAYLOAD, "supported_goals": [goal], "goal_evidence": []}
    with pytest.raises(ValidationError):
        RecipeClassification.model_validate(payload)


def test_unsupported_allergen_rejected() -> None:
    payload = {**VALID_PAYLOAD, "allergens": ["lupin"]}
    with pytest.raises(ValidationError):
        RecipeClassification.model_validate(payload)


def test_unsupported_meal_type_rejected() -> None:
    payload = {**VALID_PAYLOAD, "meal_type": "brunch"}
    with pytest.raises(ValidationError):
        RecipeClassification.model_validate(payload)


def test_duplicate_goals_normalized_with_stable_order() -> None:
    payload = {
        **VALID_PAYLOAD,
        "supported_goals": ["dairy-free", "high-protein", "dairy-free"],
        "goal_evidence": [],
    }
    classification = RecipeClassification.model_validate(payload)
    assert classification.supported_goals == [
        DietaryGoal.HIGH_PROTEIN,
        DietaryGoal.DAIRY_FREE,
    ]


def test_duplicate_allergens_normalized_with_stable_order() -> None:
    payload = {**VALID_PAYLOAD, "allergens": ["milk", "gluten", "milk"]}
    classification = RecipeClassification.model_validate(payload)
    assert classification.allergens == [Allergen.GLUTEN, Allergen.MILK]


def test_vegan_implies_vegetarian() -> None:
    classification = make_classification(goals=[DietaryGoal.VEGAN])
    assert DietaryGoal.VEGETARIAN in classification.supported_goals
    assert DietaryGoal.VEGAN in classification.supported_goals


def test_evidence_only_kept_for_supported_goals() -> None:
    payload = {
        **VALID_PAYLOAD,
        "goal_evidence": [
            {"goal": "high-protein", "reason": "Beef."},
            {"goal": "vegan", "reason": "Spurious evidence."},
        ],
    }
    classification = RecipeClassification.model_validate(payload)
    assert [e.goal for e in classification.goal_evidence] == [DietaryGoal.HIGH_PROTEIN]


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        RecipeClassification.model_validate({**VALID_PAYLOAD, "confidence": 1.4})
    with pytest.raises(ValidationError):
        RecipeClassification.model_validate({**VALID_PAYLOAD, "confidence": -0.1})
