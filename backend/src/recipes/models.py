"""Pydantic data models for recipes."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Recipe(BaseModel):
    """A normalized recipe record as stored in ``recipes.json``.

    ``dietary_tags``, ``allergens`` and ``meal_type`` are placeholders for a
    later enrichment phase and stay empty during import.
    """

    id: int = Field(gt=0)
    name: str
    author: str | None = None
    description: str = ""
    ingredients: list[str]
    instructions: list[str] = Field(default_factory=list)
    dietary_tags: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)
    meal_type: str | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("recipe name must not be empty")
        return value

    @field_validator("ingredients")
    @classmethod
    def ingredients_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("recipe must contain at least one ingredient")
        return value
