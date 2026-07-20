"""Shopping list service: merge ingredient names across selected recipes.

The source dataset has no quantities, units or serving sizes, so this
service only merges ingredient *names* — it never invents or estimates
amounts.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import BaseModel

from recipes.exceptions import RecipeNotFound
from recipes.normalization import normalize_ingredient_key
from recipes.repositories.recipe_repository import RecipeRepository

logger = logging.getLogger(__name__)


class ShoppingListItem(BaseModel):
    ingredient: str
    recipes: list[int]


class ShoppingListService:
    def __init__(self, repository: RecipeRepository) -> None:
        self._repository = repository

    def build(self, recipe_ids: Sequence[int]) -> list[ShoppingListItem]:
        """Merge the ingredients of the given recipes into one list.

        Duplicates are merged case-insensitively (Unicode-safe); the first
        occurrence keeps its original spelling. Ordering is deterministic:
        ingredients appear in first-seen order following the order of
        ``recipe_ids``; each item records every recipe (id) requiring it.

        Raises :class:`RecipeNotFound` for an unknown recipe id. An empty
        ``recipe_ids`` yields an empty list.
        """
        items: dict[str, ShoppingListItem] = {}
        seen_recipe_ids: set[int] = set()
        for recipe_id in recipe_ids:
            if recipe_id in seen_recipe_ids:
                continue
            seen_recipe_ids.add(recipe_id)
            recipe = self._repository.get_by_id(recipe_id)
            if recipe is None:
                raise RecipeNotFound(recipe_id)
            for ingredient in recipe.ingredients:
                key = normalize_ingredient_key(ingredient)
                if not key:
                    continue
                item = items.get(key)
                if item is None:
                    items[key] = ShoppingListItem(
                        ingredient=ingredient, recipes=[recipe.id]
                    )
                elif recipe.id not in item.recipes:
                    item.recipes.append(recipe.id)

        shopping_list = list(items.values())
        logger.info(
            "built shopping list: %d recipe(s) -> %d ingredient(s)",
            len(seen_recipe_ids),
            len(shopping_list),
        )
        return shopping_list
