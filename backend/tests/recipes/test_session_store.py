"""Tests for chat session state models and the in-memory session store."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from recipes.classification import DietaryGoal
from recipes.sessions import (
    ChatSession,
    InMemorySessionStore,
    MealAssignment,
    SessionState,
)


def make_meal(day_index: int, recipe_id: int, name: str = "Recept") -> MealAssignment:
    from recipes.sessions import day_label_for

    return MealAssignment(
        day_index=day_index,
        day_label=day_label_for(day_index),
        recipe_id=recipe_id,
        recipe_name=name,
        goal=DietaryGoal.HIGH_PROTEIN,
    )


class TestSessionState:
    def test_meals_sorted_by_day_index(self) -> None:
        state = SessionState(
            session_id="s1",
            meals=[make_meal(2, 30), make_meal(0, 10), make_meal(1, 20)],
        )
        assert [m.day_index for m in state.meals] == [0, 1, 2]

    def test_duplicate_day_rejected(self) -> None:
        with pytest.raises(ValidationError, match="same day"):
            SessionState(session_id="s1", meals=[make_meal(0, 10), make_meal(0, 20)])

    def test_duplicate_recipe_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate recipes"):
            SessionState(session_id="s1", meals=[make_meal(0, 10), make_meal(1, 10)])

    def test_day_label_must_match_index(self) -> None:
        with pytest.raises(ValidationError, match="does not match day_index"):
            MealAssignment(
                day_index=0,
                day_label="Day 2",
                recipe_id=10,
                recipe_name="Guláš",
                goal=DietaryGoal.HIGH_PROTEIN,
            )

    @pytest.mark.parametrize("days", [0, 32])
    def test_number_of_days_bounded(self, days: int) -> None:
        with pytest.raises(ValidationError):
            SessionState(session_id="s1", number_of_days=days)


class TestInMemorySessionStore:
    async def test_generates_session_id_when_missing(self) -> None:
        store = InMemorySessionStore()
        session = await store.get_or_create(None)
        assert session.state.session_id
        assert await store.get(session.state.session_id) is not None

    async def test_returns_existing_session(self) -> None:
        store = InMemorySessionStore()
        created = await store.get_or_create("abc-123")
        created.state.dietary_goal = DietaryGoal.VEGETARIAN
        await store.save(created)
        again = await store.get_or_create("abc-123")
        assert again.state.dietary_goal is DietaryGoal.VEGETARIAN

    async def test_get_unknown_session_returns_none(self) -> None:
        store = InMemorySessionStore()
        assert await store.get("nope") is None

    async def test_sessions_are_isolated(self) -> None:
        store = InMemorySessionStore()
        first = await store.get_or_create("one")
        first.state.dietary_goal = DietaryGoal.VEGAN
        await store.save(first)
        second = await store.get_or_create("two")
        assert second.state.dietary_goal is None

    async def test_saved_session_is_isolated_from_caller_mutations(self) -> None:
        store = InMemorySessionStore()
        session = await store.get_or_create("abc")
        session.state.meals = [make_meal(0, 10)]
        await store.save(session)
        session.state.meals.append(make_meal(1, 20))  # caller-side mutation
        stored = await store.get("abc")
        assert stored is not None
        assert [m.recipe_id for m in stored.state.meals] == [10]

    async def test_history_round_trip_without_duplication(self) -> None:
        store = InMemorySessionStore()
        session = await store.get_or_create("abc")
        session.history = ["m1", "m2"]  # type: ignore[list-item]  # opaque here
        await store.save(session)
        await store.save(await store.get_or_create("abc"))  # re-save unchanged
        stored = await store.get("abc")
        assert stored is not None
        assert stored.history == ["m1", "m2"]

    async def test_concurrent_get_or_create_creates_one_session_per_id(self) -> None:
        store = InMemorySessionStore()
        sessions = await asyncio.gather(
            *(store.get_or_create("same-id") for _ in range(20))
        )
        assert {s.state.session_id for s in sessions} == {"same-id"}
        distinct = await asyncio.gather(*(store.get_or_create(None) for _ in range(20)))
        assert len({s.state.session_id for s in distinct}) == 20

    async def test_chat_session_copy_is_independent(self) -> None:
        original = ChatSession(state=SessionState(session_id="x"))
        clone = original.copy()
        clone.state.excluded_ingredients.append("houby")
        assert original.state.excluded_ingredients == []
