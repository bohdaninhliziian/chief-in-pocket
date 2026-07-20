"""Recipe repositories: the abstraction and its JSON-backed implementation."""

from recipes.repositories.json_recipe_repository import JsonRecipeRepository
from recipes.repositories.recipe_repository import RecipeRepository

__all__ = ["JsonRecipeRepository", "RecipeRepository"]
