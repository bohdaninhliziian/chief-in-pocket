---
name: python-standards
description: Coding standards for this project - modern Python 3.11+ idioms, Pydantic v2 patterns, async rules, testing conventions, and the quality gates. Load when writing or refactoring any Python code in backend/.
---

# Python standards for Chef in My Pocket

The goal: extensible, simple, understandable. When a rule here conflicts
with cleverness, the rule wins.

## Typing (enforced by mypy)

- Full type hints on every function, including tests. `X | None`, never
  `Optional[X]`. Builtin generics (`list[str]`), `collections.abc` for
  `Sequence`/`Iterator` (not `typing`). `StrEnum` for string enums.
- Seams are `typing.Protocol`, not ABCs (see `RecipeRepository`,
  `RecipeClassifier`) — structural typing keeps implementations decoupled
  and lets tests supply plain fakes.

## Pydantic v2

- `BaseModel` at data boundaries: files, LLM output, MCP requests/responses.
  Plain `@dataclass` for internal value objects (`ImportSummary`,
  `EnrichmentOptions`, `Dependencies`).
- Idioms: `model_validate` (not parse_obj), `field_validator` /
  `model_validator(mode="after")`, `Field(default_factory=list)` for mutable
  defaults, `model_dump(mode="json")` when serializing datetimes/enums.
- Validators enforce invariants once, at the boundary — downstream code
  never re-checks (e.g. goals arrive deduped + sorted; vegan implies
  vegetarian).

## Architecture rules

- Dependency injection via constructor; nothing instantiates its own
  repository/service. Wiring happens once, at the entry point.
- Exceptions (from `recipes/exceptions.py`) only for programming/validation
  errors. "No results" is `[]`, "not found" in a repository is `None` —
  services decide whether that becomes an exception.
- LLM calls only in the offline phase (`classifier.py`, `enrichment.py`).
  Runtime code paths (repositories/services/mcp_server) are deterministic.
- `logging` module, never `print` (final CLI summaries excepted). Never log
  secrets.

## Async

- Bound concurrency with `asyncio.Semaphore`; guard shared mutations with a
  lock; no blocking I/O in async paths.
- Retries: bounded exponential backoff for transient failures only —
  classify the error first (billing/auth errors must fail fast; see
  `insufficient_quota` handling in `classifier.py`).

## Tests (pytest)

- Shared fixtures/factories in `tests/recipes/conftest.py` (`make_recipe`,
  `make_enriched`, `FakeClassifier`) — extend them, don't duplicate.
- `pytest.mark.parametrize` over copy-paste; tmp_path over real data files;
  assert specific exceptions (never bare `Exception`).
- Offline-only: `ALLOW_MODEL_REQUESTS = False` stays; LLM behavior is tested
  with `TestModel` + `agent.override`; MCP via the in-memory transport with
  the session opened inside each test.

## Files & data

- UTF-8 everywhere; JSON writes use `ensure_ascii=False`.
- Atomic writes (temp file + `os.replace`) for anything a reader might see
  mid-write.
- Comments explain *constraints the code can't show* (CSV duplicate-column
  quirk, anyio cancel-scope rule), not what the next line does.

## The gates — run before claiming anything is done

```bash
cd backend
uv run ruff check .      # lint (E,F,I,UP,B,SIM,RUF; py311; line 100)
uv run mypy src          # types (pydantic plugin, disallow_untyped_defs)
uv run pytest -q         # full suite
```

All three green, or the work is not finished.
