---
name: new-mcp-tool
description: Checklist for adding a new tool to the Chef in My Pocket MCP server the right way - service first, thin tool, dedicated response model, tests, docs. Use when adding or significantly changing MCP server capabilities.
---

# Add an MCP tool

The MCP layer stays thin. Business logic lives in services; tools only
validate → delegate → map. Work in this order:

## 1. Service method first (with unit tests)

Add the capability to `RecipeService` / `ShoppingListService` (or a new
service in `src/recipes/services/`) — repository injected via constructor,
never instantiated inside. Raise domain exceptions from
`src/recipes/exceptions.py` for real errors; return `[]`/empty for "no
results". Unit-test it against `InMemoryRepository` (see
`tests/recipes/test_recipe_service.py`) or a tmp-file `JsonRecipeRepository`.

## 2. Response model

Add a dedicated model to `src/recipes/mcp_server/models.py`. Never return
`EnrichedRecipe` or anything carrying provenance/fingerprints/evidence.
Keep search-style results lightweight (no instruction texts).

## 3. The tool

In `src/recipes/mcp_server/tools.py`, inside `register_tools`:

- wrap the body in `with _tool_call("<name>"):` (timing log + domain-error →
  `ToolError` translation);
- use enum types in the signature (`DietaryGoal`, `MealType`) so the JSON
  schema advertises valid values to the agent;
- default list params as `list[X] | None = None` and coerce with `or []`
  (ruff B006);
- write an LLM-oriented description: what it returns, when to use it, and
  cross-reference related tools.

## 4. Integration test

Add to `tests/recipes/test_mcp_server.py`. CRITICAL: open the client session
*inside* the test (`async with connect(server) as client:`) — holding it
across a pytest-asyncio fixture boundary breaks anyio cancel scopes. Test
the happy path AND at least one error path (assert `isError` and no
"Traceback" in the message).

## 5. Gates + E2E

```bash
cd backend && uv run ruff check . && uv run mypy src && uv run pytest -q
```

Then launch the `mcp-e2e-tester` agent (update its expected tool set — it
asserts the exact tool names).

## 6. Docs

Update the tool docs in `backend/CLAUDE.md` and, if the capability is
architecturally notable, `CLAUDE.md`.
