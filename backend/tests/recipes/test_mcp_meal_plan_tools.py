"""Integration tests for the meal-plan business tools over the in-memory
MCP transport.

The session store is injected so tests can assert what was actually
persisted — the canonical state, not the wire response.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import make_enriched, write_enriched_json
from mcp.shared.memory import create_connected_server_and_client_session

from recipes.classification import DietaryGoal
from recipes.mcp_server.dependencies import build_dependencies
from recipes.mcp_server.server import create_server
from recipes.sessions import InMemorySessionStore

HP = DietaryGoal.HIGH_PROTEIN
VEG = DietaryGoal.VEGETARIAN

RECIPES = [
    make_enriched(10, "Guláš", ["Hovězí plec", "Cibule"], goals=[HP]),
    make_enriched(14, "Kuřecí polévka", ["Kuřecí prsa", "Zázvor"], goals=[HP]),
    make_enriched(20, "Losos", ["Losos", "Citron"], goals=[HP]),
    make_enriched(21, "Sekaná", ["Mleté maso", "Vejce"], goals=[HP]),
    make_enriched(22, "Krůtí prsa", ["Krůtí prsa", "Rozmarýn"], goals=[HP]),
    make_enriched(25, "Sýrové rizoto", ["Rýže", "Sýr", "Houby"], goals=[VEG]),
    make_enriched(40, "Salát s tofu", ["Tofu", "Okurka"], goals=[VEG]),
]


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def server(tmp_path: Path, store: InMemorySessionStore) -> Any:
    data_path = write_enriched_json(tmp_path / "enriched.json", RECIPES)
    deps = build_dependencies(data_path, session_store=store)
    return create_server(dependencies=deps)


def connect(server: Any) -> Any:
    """In-memory client session; entered inside each test so anyio cancel
    scopes stay within a single task."""
    return create_connected_server_and_client_session(server._mcp_server)


def structured(result: Any) -> dict[str, Any]:
    assert result.isError is False, result.content
    return result.structuredContent


async def test_create_meal_plan_persists_state(
    server: Any, store: InMemorySessionStore
) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 3, "meals_per_day": 1},
        )
    plan = structured(result)
    assert [m["recipe_id"] for m in plan["meals"]] == [10, 14, 20]
    assert [m["day_label"] for m in plan["meals"]] == [
        "Day 1",
        "Day 2",
        "Day 3",
    ]
    session = await store.get("abc")
    assert session is not None
    assert session.state.dietary_goal is HP
    assert session.state.number_of_days == 3
    assert [m.recipe_id for m in session.state.meals] == [10, 14, 20]
    assert session.state.shopping_list  # rebuilt and stored


async def test_create_plan_reports_shortfall(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "vegetarian", "days": 5, "meals_per_day": 1},
        )
    plan = structured(result)
    assert plan["requested_days"] == 5
    assert plan["planned_days"] == 2


async def test_create_plan_zero_matches_is_clean_error(
    server: Any, store: InMemorySessionStore
) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "low-carb", "days": 3, "meals_per_day": 1},
        )
    assert result.isError is True
    text = result.content[0].text
    assert "low-carb" in text and "Traceback" not in text
    session = await store.get("abc")
    assert session is not None and session.state.meals == []  # nothing invalid saved


async def test_replace_meal_swaps_only_target_day(
    server: Any, store: InMemorySessionStore
) -> None:
    async with connect(server) as client:
        await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 3, "meals_per_day": 1},
        )
        result = await client.call_tool(
            "replace_meal",
            {"session_id": "abc", "day_index": 1, "goal": "vegetarian"},
        )
    plan = structured(result)
    assert [m["recipe_id"] for m in plan["meals"]] == [10, 25, 20]
    session = await store.get("abc")
    assert session is not None
    assert [m.recipe_id for m in session.state.meals] == [10, 25, 20]
    assert session.state.dietary_goal is HP  # plan goal unchanged
    recipes_on_list = {
        rid for item in session.state.shopping_list for rid in item.recipes
    }
    assert recipes_on_list == {10, 25, 20}  # shopping list rebuilt


async def test_failed_replace_preserves_stored_state(
    server: Any, store: InMemorySessionStore
) -> None:
    async with connect(server) as client:
        await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 3, "meals_per_day": 1},
        )
        before = await store.get("abc")
        result = await client.call_tool(
            "replace_meal",
            {"session_id": "abc", "day_index": 6},  # not part of the 3-day plan
        )
    assert result.isError is True
    assert "planned days" in result.content[0].text
    after = await store.get("abc")
    assert before is not None and after is not None
    assert after.state == before.state


async def test_default_three_meals_per_day_with_slots(
    server: Any, store: InMemorySessionStore
) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 1},
        )
    plan = structured(result)
    # meals_per_day defaults to 3: day 1 gets slots 0..2
    assert [(m["day_index"], m["slot"]) for m in plan["meals"]] == [
        (0, 0),
        (0, 1),
        (0, 2),
    ]
    assert plan["meals_per_day"] == 3
    assert plan["planned_days"] == 1
    session = await store.get("abc")
    assert session is not None and session.state.meals_per_day == 3


async def test_partial_final_day_shortfall_is_visible(server: Any) -> None:
    """5 high-protein recipes over 2 days x 3 meals: planned_days alone
    hides the missing day-2 meal; planned_meals must expose it."""
    async with connect(server) as client:
        result = await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 2},
        )
    plan = structured(result)
    assert plan["planned_days"] == plan["requested_days"] == 2
    assert plan["requested_meals"] == 6
    assert plan["planned_meals"] == 5  # the shortfall signal


async def test_replace_requires_slot_on_multi_meal_day(server: Any) -> None:
    async with connect(server) as client:
        await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 1},
        )
        missing = await client.call_tool(
            "replace_meal", {"session_id": "abc", "day_index": 0}
        )
        assert missing.isError is True
        assert "specify slot" in missing.content[0].text
        replaced = await client.call_tool(
            "replace_meal",
            {"session_id": "abc", "day_index": 0, "slot": 1, "goal": "vegetarian"},
        )
    plan = structured(replaced)
    by_slot = {m["slot"]: m for m in plan["meals"]}
    assert by_slot[1]["goal"] == "vegetarian"
    assert by_slot[0]["goal"] == "high-protein"  # siblings preserved
    assert by_slot[2]["goal"] == "high-protein"


async def test_replace_before_plan_is_clean_error(server: Any) -> None:
    async with connect(server) as client:
        result = await client.call_tool(
            "replace_meal", {"session_id": "nope", "day_index": 0}
        )
    assert result.isError is True
    assert "no meal plan exists yet" in result.content[0].text


async def test_get_meal_plan_returns_current_state(server: Any) -> None:
    async with connect(server) as client:
        await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 2, "meals_per_day": 1},
        )
        result = await client.call_tool("get_meal_plan", {"session_id": "abc"})
    plan = structured(result)
    assert plan["dietary_goal"] == "high-protein"
    assert len(plan["meals"]) == 2


async def test_exclusion_is_store_only_and_reports_affected_days(
    server: Any, store: InMemorySessionStore
) -> None:
    async with connect(server) as client:
        await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 3, "meals_per_day": 1},
        )
        result = await client.call_tool(
            "add_ingredient_exclusion",
            {"session_id": "abc", "ingredient": "Cibule"},
        )
    response = structured(result)
    assert response["excluded_ingredients"] == ["Cibule"]
    assert response["affected_days"] == ["Day 1"]  # Guláš contains Cibule
    session = await store.get("abc")
    assert session is not None
    assert [m.recipe_id for m in session.state.meals] == [10, 14, 20]  # unchanged
    assert session.state.excluded_ingredients == ["Cibule"]


async def test_exclusion_applies_to_later_replacement(
    server: Any, store: InMemorySessionStore
) -> None:
    async with connect(server) as client:
        await client.call_tool(
            "create_meal_plan",
            {"session_id": "abc", "goal": "high-protein", "days": 3, "meals_per_day": 1},
        )
        await client.call_tool(
            "add_ingredient_exclusion",
            {"session_id": "abc", "ingredient": "houby"},
        )
        result = await client.call_tool(
            "replace_meal",
            {"session_id": "abc", "day_index": 0, "goal": "vegetarian"},
        )
    plan = structured(result)
    # rizoto (25) is first by id but contains houby -> tofu salad (40) chosen
    assert plan["meals"][0]["recipe_id"] == 40


async def test_sessions_are_isolated(
    server: Any, store: InMemorySessionStore
) -> None:
    async with connect(server) as client:
        await client.call_tool(
            "create_meal_plan",
            {"session_id": "one", "goal": "high-protein", "days": 2, "meals_per_day": 1},
        )
        result = await client.call_tool("get_meal_plan", {"session_id": "two"})
    assert result.isError is True  # session "two" has no plan
