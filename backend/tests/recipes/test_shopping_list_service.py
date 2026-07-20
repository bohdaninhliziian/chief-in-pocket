"""Tests for ShoppingListService ingredient merging."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_enriched, write_enriched_json

from recipes.exceptions import RecipeNotFound
from recipes.repositories import JsonRecipeRepository
from recipes.services import ShoppingListService

GULAS = make_enriched(10, "Guláš", ["Hovězí plec", "Máslo", "Mléko", "Cibule"])
SOUP = make_enriched(14, "Polévka", [" máslo ", "Kokosové mléko", "Zázvor"])
CAKE = make_enriched(20, "Bábovka", ["Mouka", "MLÉKO", "Vejce", "Máslo"])


@pytest.fixture
def service(tmp_path: Path) -> ShoppingListService:
    repository = JsonRecipeRepository(
        write_enriched_json(tmp_path / "enriched.json", [GULAS, SOUP, CAKE])
    )
    return ShoppingListService(repository=repository)


def test_merges_duplicates_and_tracks_recipes(service: ShoppingListService) -> None:
    items = service.build([10, 14, 20])
    by_name = {item.ingredient: item.recipes for item in items}
    # "Máslo" / " máslo " / "Máslo" merge into one entry, first spelling kept.
    assert by_name["Máslo"] == [10, 14, 20]
    # "Mléko" and "MLÉKO" merge; "Kokosové mléko" stays separate.
    assert by_name["Mléko"] == [10, 20]
    assert by_name["Kokosové mléko"] == [14]
    assert by_name["Vejce"] == [20]


def test_order_is_deterministic_first_seen(service: ShoppingListService) -> None:
    items = service.build([10, 14, 20])
    assert [item.ingredient for item in items] == [
        "Hovězí plec",
        "Máslo",
        "Mléko",
        "Cibule",
        "Kokosové mléko",
        "Zázvor",
        "Mouka",
        "Vejce",
    ]
    # Same input always yields the same output.
    assert items == service.build([10, 14, 20])


def test_recipe_order_drives_ingredient_order(
    service: ShoppingListService,
) -> None:
    items = service.build([20, 10])
    assert items[0].ingredient == "Mouka"
    assert items[1].ingredient == "MLÉKO"  # first-seen spelling from recipe 20
    assert items[1].recipes == [20, 10]


def test_empty_recipe_list_returns_empty(service: ShoppingListService) -> None:
    assert service.build([]) == []


def test_invalid_recipe_id_raises(service: ShoppingListService) -> None:
    with pytest.raises(RecipeNotFound, match="999"):
        service.build([10, 999])


def test_duplicate_recipe_ids_counted_once(service: ShoppingListService) -> None:
    items = service.build([10, 10])
    by_name = {item.ingredient: item.recipes for item in items}
    assert by_name["Máslo"] == [10]
