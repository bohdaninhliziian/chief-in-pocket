"""Integration tests for the MCP server over the in-memory transport.

A real client session talks to the real server (initialize handshake, tool
discovery, tool calls) — only the transport is in-memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import make_enriched, write_enriched_json
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

from recipes.classification import Allergen, DietaryGoal, MealType
from recipes.mcp_server.server import create_server
from recipes.services.recipe_service import MAX_RECIPES

RECIPES = [
    make_enriched(
        10,
        "Hovězí guláš",
        ["Hovězí plec", "Máslo", "Mléko", "Knedlík"],
        goals=[DietaryGoal.HIGH_PROTEIN],
        allergens=[Allergen.GLUTEN, Allergen.MILK],
    ),
    make_enriched(
        14,
        "Kuřecí polévka",
        ["Kuřecí prsa", "Kokosové mléko", "Zázvor", "Máslo"],
        goals=[DietaryGoal.HIGH_PROTEIN, DietaryGoal.DAIRY_FREE],
        meal_type=MealType.SOUP,
    ),
    make_enriched(
        40,
        "Salát s tofu",
        ["Tofu", "Okurka"],
        goals=[DietaryGoal.VEGETARIAN, DietaryGoal.VEGAN],
        meal_type=MealType.SALAD,
    ),
]


@pytest.fixture
def server(tmp_path: Path) -> Any:
    data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
    return create_server(data_path)  # also covers "server starts"


def connect(server: Any) -> Any:
    """In-memory client session; entered inside each test so anyio cancel
    scopes stay within a single task (pytest-asyncio fixture teardown runs
    in a different task, which anyio forbids)."""
    return create_connected_server_and_client_session(server._mcp_server)


def rows(result: Any) -> Any:
    """Unwrap structured content ({'result': [...]} for list returns)."""
    assert result.isError is False, result.content
    structured = result.structuredContent
    return structured.get("result", structured)


async def test_tool_discovery(server: Any) -> None:
    async with connect(server) as client:
        listed = await client.list_tools()
        by_name = {tool.name: tool for tool in listed.tools}
        assert set(by_name) == {
            "list_supported_goals",
            "search_recipes",
            "get_recipe",
            "build_shopping_list",
            "create_meal_plan",
            "replace_meal",
            "get_meal_plan",
            "add_ingredient_exclusion",
        }
        for tool in by_name.values():
            assert tool.description  # every tool documents itself
        goal_schema = by_name["search_recipes"].inputSchema
        assert "goal" in goal_schema["properties"]


async def test_list_supported_goals(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool("list_supported_goals", {})
        assert rows(result) == [
            "high-protein",
            "low-carb",
            "vegetarian",
            "vegan",
            "pescatarian",
            "gluten-free",
            "dairy-free",
        ]


async def test_search_recipes_returns_summaries(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "search_recipes", {"goal": "high-protein", "count": 5}
        )
        summaries = rows(result)
        assert [s["id"] for s in summaries] == [10, 14]
        assert summaries[0]["name"] == "Hovězí guláš"
        assert summaries[0]["supported_goals"] == ["high-protein"]
        # Summaries stay lightweight: no instructions or description leak out.
        assert "instructions" not in summaries[0]
        assert "description" not in summaries[0]
        assert "classification" not in summaries[0]


async def test_search_with_excluded_ingredients(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "search_recipes",
            {"goal": "high-protein", "count": 5, "excluded_ingredients": [" mléko "]},
        )
        assert [s["id"] for s in rows(result)] == [14]


async def test_search_with_meal_type(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "search_recipes", {"goal": "high-protein", "count": 5, "meal_type": "soup"}
        )
        assert [s["id"] for s in rows(result)] == [14]


async def test_empty_search_returns_empty_list(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "search_recipes", {"goal": "pescatarian", "count": 3}
        )
        assert rows(result) == []


async def test_search_rejects_unsupported_goal(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool("search_recipes", {"goal": "keto", "count": 3})
        assert result.isError is True


async def test_search_rejects_count_above_maximum(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "search_recipes", {"goal": "high-protein", "count": MAX_RECIPES + 1}
        )
        assert result.isError is True
        message = "".join(
            c.text for c in result.content if isinstance(c, TextContent)
        )
        assert f"between 1 and {MAX_RECIPES}" in message
        assert "Traceback" not in message


async def test_get_recipe_returns_full_detail(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool("get_recipe", {"recipe_id": 10})
        detail = rows(result)
        assert detail["name"] == "Hovězí guláš"
        assert detail["ingredients"] == ["Hovězí plec", "Máslo", "Mléko", "Knedlík"]
        assert detail["instructions"] == ["Vše smícháme a podáváme."]
        assert detail["supported_goals"] == ["high-protein"]
        assert detail["allergens"] == ["gluten", "milk"]
        assert detail["meal_type"] == "main-course"
        # Internal provenance is not exposed over MCP.
        assert "classification" not in detail
        assert "classification_evidence" not in detail


async def test_get_recipe_invalid_id_is_clean_error(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool("get_recipe", {"recipe_id": 999})
        assert result.isError is True
        message = "".join(
            c.text for c in result.content if isinstance(c, TextContent)
        )
        assert "999" in message and "Traceback" not in message


async def test_build_shopping_list(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "build_shopping_list", {"recipe_ids": [10, 14]}
        )
        entries = rows(result)
        by_name = {entry["ingredient"]: entry["recipes"] for entry in entries}
        assert by_name["Máslo"] == [10, 14]  # merged across both recipes
        assert by_name["Kokosové mléko"] == [14]


async def test_build_shopping_list_invalid_id(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool("build_shopping_list", {"recipe_ids": [999]})
        assert result.isError is True


async def test_czech_characters_survive_the_wire(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool("get_recipe", {"recipe_id": 10})
        assert rows(result)["name"] == "Hovězí guláš"
