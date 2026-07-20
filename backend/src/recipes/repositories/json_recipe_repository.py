"""JSON-file-backed recipe repository.

Loads ``recipes_enriched.json`` exactly once at construction, validates
every record through the existing :class:`recipes.classification
.EnrichedRecipe` model, and serves all queries from memory. The source
file is never modified.

Search is deterministic: plain attribute filtering over a few hundred
in-memory records, no LLM, no semantic matching, results ordered by
recipe id.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from recipes.classification import DietaryGoal, EnrichedRecipe, MealType
from recipes.exceptions import RecipeDataError
from recipes.normalization import normalize_ingredient_key

logger = logging.getLogger(__name__)


class JsonRecipeRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._recipes = self._load(path)
        self._by_id = {recipe.id: recipe for recipe in self._recipes}
        # Precomputed normalized-ingredient sets for exclusion matching.
        self._ingredient_keys = {
            recipe.id: {normalize_ingredient_key(i) for i in recipe.ingredients}
            for recipe in self._recipes
        }
        logger.info("loaded %d recipes from %s", len(self._recipes), path)

    @staticmethod
    def _load(path: Path) -> list[EnrichedRecipe]:
        if not path.is_file():
            raise RecipeDataError(f"recipe data file not found: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RecipeDataError(f"cannot read recipe data from {path}: {exc}") from exc
        if not isinstance(raw, list):
            raise RecipeDataError(f"{path} must contain a JSON array of recipes")

        recipes: list[EnrichedRecipe] = []
        seen_ids: set[int] = set()
        for index, item in enumerate(raw):
            try:
                recipe = EnrichedRecipe.model_validate(item)
            except ValidationError as exc:
                raise RecipeDataError(
                    f"invalid recipe at index {index} in {path}: {exc}"
                ) from exc
            if recipe.id in seen_ids:
                raise RecipeDataError(
                    f"duplicate recipe id {recipe.id} in {path}"
                )
            seen_ids.add(recipe.id)
            recipes.append(recipe)
        return sorted(recipes, key=lambda recipe: recipe.id)

    def get_by_id(self, recipe_id: int) -> EnrichedRecipe | None:
        return self._by_id.get(recipe_id)

    def search(
        self,
        *,
        goal: DietaryGoal,
        limit: int,
        excluded_ingredients: Sequence[str] = (),
        excluded_recipe_ids: Sequence[int] = (),
        meal_type: MealType | None = None,
    ) -> list[EnrichedRecipe]:
        excluded_keys = {
            key
            for key in (normalize_ingredient_key(i) for i in excluded_ingredients)
            if key
        }
        excluded_ids = set(excluded_recipe_ids)
        matches: list[EnrichedRecipe] = []
        for recipe in self._recipes:  # already ordered by id → deterministic
            if len(matches) >= limit:
                break
            if recipe.id in excluded_ids:
                continue
            if goal not in recipe.supported_goals:
                continue
            if meal_type is not None and recipe.meal_type is not meal_type:
                continue
            if excluded_keys and self._ingredient_keys[recipe.id] & excluded_keys:
                continue
            matches.append(recipe)
        return matches

    def all(self) -> list[EnrichedRecipe]:
        return list(self._recipes)
