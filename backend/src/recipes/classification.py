"""Enums and Pydantic models for LLM-based recipe classification."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from recipes.models import Recipe


class DietaryGoal(StrEnum):
    """The complete, closed set of dietary goals supported by the MVP."""

    HIGH_PROTEIN = "high-protein"
    LOW_CARB = "low-carb"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    PESCATARIAN = "pescatarian"
    GLUTEN_FREE = "gluten-free"
    DAIRY_FREE = "dairy-free"


class Allergen(StrEnum):
    GLUTEN = "gluten"
    MILK = "milk"
    EGGS = "eggs"
    NUTS = "nuts"
    PEANUTS = "peanuts"
    FISH = "fish"
    SHELLFISH = "shellfish"
    SOY = "soy"
    CELERY = "celery"
    MUSTARD = "mustard"
    SESAME = "sesame"


class MealType(StrEnum):
    MAIN_COURSE = "main-course"
    SOUP = "soup"
    DESSERT = "dessert"
    SIDE_DISH = "side-dish"
    BREAKFAST = "breakfast"
    SALAD = "salad"
    OTHER = "other"


class ReviewStatus(StrEnum):
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs-review"
    FAILED = "failed"


_GOAL_ORDER = {goal: index for index, goal in enumerate(DietaryGoal)}
_ALLERGEN_ORDER = {allergen: index for index, allergen in enumerate(Allergen)}


def sort_goals(goals: list[DietaryGoal]) -> list[DietaryGoal]:
    """Deduplicate and order goals by enum declaration order (stable)."""
    return sorted(set(goals), key=_GOAL_ORDER.__getitem__)


def sort_allergens(allergens: list[Allergen]) -> list[Allergen]:
    """Deduplicate and order allergens by enum declaration order (stable)."""
    return sorted(set(allergens), key=_ALLERGEN_ORDER.__getitem__)


class GoalEvidence(BaseModel):
    goal: DietaryGoal
    reason: str


class RecipeClassification(BaseModel):
    """Structured output produced by the classifier for one recipe."""

    supported_goals: list[DietaryGoal]
    allergens: list[Allergen]
    meal_type: MealType
    confidence: float = Field(ge=0.0, le=1.0)
    goal_evidence: list[GoalEvidence]
    allergen_reason: str
    meal_type_reason: str

    @field_validator("supported_goals")
    @classmethod
    def normalize_goals(cls, value: list[DietaryGoal]) -> list[DietaryGoal]:
        # Every vegan recipe is by definition also vegetarian.
        if DietaryGoal.VEGAN in value and DietaryGoal.VEGETARIAN not in value:
            value = [*value, DietaryGoal.VEGETARIAN]
        return sort_goals(value)

    @field_validator("allergens")
    @classmethod
    def normalize_allergens(cls, value: list[Allergen]) -> list[Allergen]:
        return sort_allergens(value)

    @model_validator(mode="after")
    def evidence_only_for_supported_goals(self) -> RecipeClassification:
        supported = set(self.supported_goals)
        self.goal_evidence = [
            evidence for evidence in self.goal_evidence if evidence.goal in supported
        ]
        return self


class ClassificationMetadata(BaseModel):
    """Provenance of a classification, stored next to the domain fields."""

    source: Literal["llm", "rules", "manual", "hybrid"]
    model: str | None
    classifier_version: str
    prompt_version: str
    classified_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus
    fingerprint: str
    validation_warnings: list[str] = Field(default_factory=list)


class ClassificationEvidence(BaseModel):
    goal_evidence: list[GoalEvidence] = Field(default_factory=list)
    allergen_reason: str = ""
    meal_type_reason: str = ""


class EnrichedRecipe(BaseModel):
    """A recipe together with its classification and provenance.

    All original recipe fields are preserved verbatim; ``supported_goals``,
    ``allergens`` and ``meal_type`` replace the empty importer placeholders.
    """

    id: int
    name: str
    author: str | None
    description: str
    ingredients: list[str]
    instructions: list[str]
    supported_goals: list[DietaryGoal]
    allergens: list[Allergen]
    meal_type: MealType | None
    classification: ClassificationMetadata
    classification_evidence: ClassificationEvidence

    @classmethod
    def from_parts(
        cls,
        recipe: Recipe,
        classification: RecipeClassification,
        metadata: ClassificationMetadata,
    ) -> EnrichedRecipe:
        """Combine a source recipe with its classification and provenance."""
        return cls(
            id=recipe.id,
            name=recipe.name,
            author=recipe.author,
            description=recipe.description,
            ingredients=list(recipe.ingredients),
            instructions=list(recipe.instructions),
            supported_goals=classification.supported_goals,
            allergens=classification.allergens,
            meal_type=classification.meal_type,
            classification=metadata,
            classification_evidence=ClassificationEvidence(
                goal_evidence=classification.goal_evidence,
                allergen_reason=classification.allergen_reason,
                meal_type_reason=classification.meal_type_reason,
            ),
        )
