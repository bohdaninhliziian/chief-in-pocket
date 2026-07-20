# Chef in My Pocket — backend guide

uv-managed, Python ≥ 3.11, Pydantic v2, Pydantic AI, official `mcp` SDK.
This file is the backend's technical deep-dive (it replaced
`backend/README.md`). Root `CLAUDE.md` holds the project-wide rules;
`Doc.md` at the root is the system-design deliverable.

> **Keep this file current** — same rule as the root CLAUDE.md.

## Architecture

```
PHASE 1 offline: data/raw/recipes.csv → scripts/import_recipes.py → data/processed/recipes.json
PHASE 2 offline: recipes.json → scripts/enrich_recipes_llm.py (LLM) → recipes_enriched.json
PHASE 3 runtime: recipes_enriched.json → repository → services → MCP server (NO LLM)
PHASE 4 runtime: chat API → Pydantic AI agent (the ONLY runtime LLM) → in-process
                 MCP business tools → deterministic services → session store
```

The classification LLM runs **only** in phase 2. Never add LLM calls,
semantic search or network I/O to repositories/services/mcp_server.
The chat agent interprets intent and picks a tool; all state changes run
in deterministic Python. `session_id` on business tools is force-injected
by a `process_tool_call` hook — the model can never target another session.

## Layout (`src/recipes/`)

- `models.py` — `Recipe` (imported, pre-enrichment)
- `normalization.py` — pure text helpers; `normalize_ingredient_key` is the
  canonical ingredient-comparison key (NFC + whitespace + casefold);
  `split_instructions` (boundary regex + min step length, configurable)
- `importer.py` — CSV → JSON; skip-and-log invalid rows, never abort batch
- `classification.py` — enums (`DietaryGoal`, `Allergen`, `MealType`,
  `ReviewStatus`) + `RecipeClassification` (LLM output schema),
  `EnrichedRecipe`, provenance metadata
- `recipe_classifier_prompt.py` — system prompt + `CLASSIFIER_VERSION` /
  `CLASSIFIER_PROMPT_VERSION` (bumping either invalidates the cache)
- `classifier.py` — `RecipeClassifier` protocol + `PydanticAiRecipeClassifier`
  (Agent with `output_type=RecipeClassification`, `defer_model_check=True`,
  bounded backoff; `insufficient_quota` fails fast — billing, not rate limit)
- `enrichment.py` — pipeline: SHA-256 fingerprint cache, resume by default,
  atomic incremental writes, semaphore concurrency, confidence-threshold
  gate (below threshold ⇒ `needs-review`), report
- `exceptions.py`, `repositories/`, `services/` — runtime layer
  (Repository Pattern; services take the repository via constructor);
  `services/meal_plan_service.py` — deterministic plan create/replace
  (pure function of inputs, never touches storage; days are a generic
  0-based sequence labelled "Day 1".."Day N" via `day_label_for` — never
  calendar weekdays)
- `sessions/` — `SessionState` (validated canonical truth: goal, days,
  meals_per_day, meal assignments keyed by (day_index, slot), exclusions,
  shopping list — rejects duplicate day-slots/recipes) + `SessionStore`
  protocol + `InMemorySessionStore` (volatile; Redis/Postgres slot in
  behind the protocol). Plans: 1–31 days (`MAX_PLAN_DAYS`), 1–3 meals/day
  (default 3, generic "meal N of the day" slots, filled day-major so
  shortfalls truncate the tail); `MAX_RECIPES` = 31 × 3 = 93.
- `mcp_server/` — thin FastMCP wrapper: `server.py` (factory; accepts
  pre-built `Dependencies` so the chat API shares the session store),
  `tools.py` (4 read tools), `meal_plan_tools.py` (4 business tools),
  `dependencies.py` (wired once at startup), `models.py` (lean response
  models; internal provenance never crosses the wire)
- `chat/` — `agent.py` (single Pydantic AI agent, in-process `MCPToolset`,
  session-id injection, `UsageLimits`; structured output
  `ChatOutput {message, voice_summary}` via `NativeOutput`), `history.py`
  (per-session trim at user-turn boundaries), `transcription.py` (STT),
  `synthesis.py` (TTS), `api.py` (FastAPI app factory: POST /chat,
  /transcribe, /speak, GET /recipes/{id}, dev helpers GET /sessions/{id}
  + /sessions/{id}/history; CORS for the Vite dev server)

## Phase 1 — importer

```bash
uv run python scripts/import_recipes.py        # default --limit 20
```

Flags: `--start-id N`, `--end-id N`, `--limit N` (0 = no limit),
`--pretty`, `--fail-on-invalid` (exit non-zero on any skipped row).

CSV (`data/raw/recipes.csv`): UTF-8, comma-delimited, quoted fields may
contain commas/newlines. Header is `id,name,name,author_note,ingredients,steps`
— **duplicate `name` column** (2nd is the author, e.g. "Roman Vaněk").
Parse positionally with `csv.reader`, never `DictReader`:
0=id, 1=name, 2=author, 3=description (may contain HTML), 4=ingredients
(one comma-separated string), 5=steps.

Steps are separated by *period-then-comma* (`"…kostky., Jakmile…"`, regex
`(?<=\.)\s*,\s+`) plus blank lines — never bare commas or periods.
Fragments < 15 chars merge into the previous step; a cell with no boundary
stays one instruction. Invalid rows (bad id, empty name, no ingredients,
wrong column count) are skipped with a logged warning incl. line number;
system errors (missing file, broken encoding) still abort.

Output: JSON array with `dietary_tags`/`allergens`/`meal_type` empty —
phase 2 fills them. All JSON writes use `ensure_ascii=False`.

## Phase 2 — LLM enrichment (offline classification)

**Costs money.** Never run over the full dataset unprompted; use
`--dry-run` or `--limit N`. See the **/enrich** skill.

```bash
uv run python scripts/enrich_recipes_llm.py --dry-run          # no API calls
uv run python scripts/enrich_recipes_llm.py --limit 5 --pretty # small sample
```

Flags: `--input/--output/--report`, `--model` (or
`RECIPE_CLASSIFIER_MODEL`, default `openai:gpt-5-mini`), `--concurrency`,
`--force` (ignore cache), `--max-retries N`, `--confidence-threshold`
(default 0.70), `--strict`.

**Dietary goals — a closed set of exactly 7** (never invent keto/paleo/…):
`vegetarian`, `vegan` (⇒ vegetarian), `pescatarian` (**must contain
fish/seafood**, no other meat), `gluten-free`, `dairy-free` (coconut milk
is fine, eggs are not dairy), `high-protein`, `low-carb`. High-protein and
low-carb are ingredient-based candidate signals — the dataset has no
quantities/nutrition, so they are never nutritionally verified.

**Caching & versioning:** each result stores a SHA-256 fingerprint of
recipe id + normalized name + ingredients + model +
`CLASSIFIER_VERSION` + `CLASSIFIER_PROMPT_VERSION`. Matching fingerprints
are skipped on rerun; changing any input reclassifies just those recipes.
Output is written after every finished recipe via atomic temp-file
replace, so interrupted runs keep completed work. After editing the
prompt: bump `CLASSIFIER_PROMPT_VERSION` (see **/bump-classifier**).

**Review gate:** classification is LLM-only. Structural validity comes
from the Pydantic schema (closed enums, vegan ⇒ vegetarian, evidence
pruned to supported goals); below-threshold confidence ⇒
`review_status: "needs-review"` + warning, never silently accepted.
Values: `accepted` / `needs-review` / `failed`. Each result carries
`classification_evidence` (reason per goal, allergen + meal-type
rationale) so results stay auditable.

**Cost:** only id + name + ingredients are sent (~300 input tokens per
recipe); the report aggregates token usage with a best-effort cost
estimate via `genai-prices`.

### Reliability roadmap (deferred, language-neutral)

- **Pluggable per-locale validation rulesets** — protocol
  `validate(recipe, classification) -> ValidationResult`, one impl per
  locale with its own keyword stems + false-positive exceptions, each
  backed by regression tests.
- **Verifier LLM pass** — second, independently prompted call challenging
  the first classification; disagreements route to `needs-review`.
- **Retry with feedback** — re-ask the classifier once with validator
  findings before flagging (existed in the removed prototype).
- **Allergen safety net** — deterministic detection that merges missed
  allergens in (over-report, never under-report).
- **Human review queue** — approve/fix/reclassify flow for `needs-review`.

## Phase 3 — runtime recipe layer

No LLM, no semantic search, no database: `JsonRecipeRepository` loads
`recipes_enriched.json` once at startup, validates every record (rejects
invalid JSON and duplicate ids), serves all queries from memory; the file
is never re-read or modified. Services depend only on the
`RecipeRepository` protocol (`get_by_id`, `search`, `all`) — a
`PostgresRecipeRepository` would change nothing above it.

Rules: at most 93 recipes per search (`MAX_RECIPES`); fewer or zero
matches return a short/empty list, **never** an error. Exceptions are for
real mistakes only: `InvalidDietaryGoal`, `InvalidMealType`,
`InvalidRecipeCount`, `RecipeNotFound`, `RecipeDataError`. Ingredient
matching normalizes Unicode (NFC) + whitespace + case (`" mléko "` ==
`"Mléko"`); original spellings preserved in output. Shopping lists merge
ingredient **names only** (no quantities exist — never invent them); each
entry lists the recipe ids that need it, deterministic first-seen order.

## MCP server

Thin wrapper over the services (official `mcp` SDK, FastMCP). Dependencies
are wired once at startup (`dependencies.py`); tools return dedicated
response models; domain exceptions become clean MCP tool errors, never
tracebacks; zero matches return `[]`.

Read tools: `list_supported_goals` (the 7 goals) · `search_recipes`
(goal, count 1–93, excluded_ingredients?, meal_type? → summaries) ·
`get_recipe` (full detail incl. allergens) · `build_shopping_list`
(recipe_ids → merged ingredients).

Business tools (each runs load → service → validate → save, so a failed
op preserves the previous valid plan): `create_meal_plan` (session_id,
goal, days 1–31, meals_per_day? 1–3 → full plan + shopping list) ·
`replace_meal` (day_index, slot? — required when the day has several
meals; other meals preserved, no duplicates, exclusions applied) ·
`get_meal_plan` · `add_ingredient_exclusion` (store-only: reports which
current plan days contain the ingredient, existing meals unchanged).

```bash
uv run python scripts/run_mcp_server.py        # stdio; RECIPES_ENRICHED_PATH to override data
npx @modelcontextprotocol/inspector uv run python scripts/run_mcp_server.py
```

Inspector check: List Tools shows all 8; `search_recipes
goal=high-protein count=3` works; invalid input returns a clean error.
Claude Desktop config: command `uv`, args `run --project
/abs/path/backend python /abs/path/backend/scripts/run_mcp_server.py`.

## Phase 4 — chat agent

Flow: POST /chat → load session (history + state) → Pydantic AI agent →
LLM picks an MCP tool → in-process server → deterministic workflow
(search → assign → shopping list → validate `SessionState` → save) →
final response + structured `meal_plan`. Standard tool loop, capped per
message at 8 model requests / 12 tool calls (`UsageLimits`) + model
timeout — limits return clean HTTP 502.

Message history = conversational continuity; structured `SessionState` =
canonical plan. Never rely on the LLM to reconstruct the plan from chat.

Design decisions:
- **High-level business tools** — the LLM supplies intent (goal, days,
  day_index); the whole workflow runs in `MealPlanService`. Business rules
  (no duplicate recipes, preserve untouched meals, apply exclusions,
  rebuild list) live in code + tests, not prompts.
- **In-process MCP transport** — the chat API hands the same `FastMCP`
  object to `MCPToolset`; one process, shared session store; stdio mode
  stays available with identical tools.
- **Session-id injection** — `process_tool_call` overwrites `session_id`
  from a request `ContextVar`.
- **Guardrails in code** — ingredient exclusions are stored in the session
  and applied to every subsequent search (the model cannot forget them);
  allergen questions are answered from stored 11-category data via
  `get_recipe` (system prompt forbids inventing allergen info).
  Category-level allergen *filtering* in search is deliberately deferred.

```bash
uv run python scripts/run_chat_api.py     # http://127.0.0.1:8000 (needs OPENAI_API_KEY)
uv run python scripts/chat_demo.py        # interactive CLI client
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"message": "Create a high-protein plan for three days."}'
curl -s localhost:8000/sessions/<id>           # structured state (dev)
curl -s localhost:8000/sessions/<id>/history   # raw transcript incl. tool calls (dev)
```

## Voice (ElevenLabs STT + TTS)

Voice input is an adapter: mic → POST /transcribe → transcript into the
chat input → user edits → Send → normal /chat path. The transcript is
**never auto-sent**. STT behind `SpeechTranscriber` (ElevenLabs Scribe,
`scribe_v1`); without `ELEVENLABS_API_KEY`, /transcribe and /speak return
503 and the UI stays text-only. STT language pinned via
`CHAT_STT_LANGUAGE` (default `cs`) — unhinted STT hallucinates on
short/silent audio, **never revert to auto-detect**; sub-1KB uploads are
rejected as silence (frontend drops recordings < 500 ms). Known problem:
Czech transcription accuracy is still poor even pinned.

Voice output is on-demand: every reply includes a self-contained
`voice_summary` (speaks the full plan — hands-free cooking, ~40 extra
tokens); the speaker button calls POST /speak → ElevenLabs TTS
(`eleven_flash_v2_5`) → mp3, cached client-side per message (replays
free). `/speak` caps input at 4000 chars (`MAX_SPEAK_CHARS`). Both STT
and TTS are ElevenLabs-only by choice; a second vendor slots in behind
`SpeechTranscriber`/`SpeechSynthesizer` without touching the API layer.

## Testing & quality gates (all three before claiming done)

```bash
uv run ruff check .    # lint: E,F,I,UP,B,SIM,RUF · py311 · line 100
uv run mypy src        # pydantic plugin, disallow_untyped_defs
uv run pytest -q       # full suite, all offline
```

- Tests never call a real LLM/provider: `conftest.py` sets
  `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`; use `FakeClassifier`,
  `TestModel` + `agent.override`, `FunctionModel`, or the in-memory MCP
  transport. Shared fixtures in `tests/recipes/conftest.py`
  (`make_recipe`, `make_enriched`, `make_classification`, `FakeClassifier`).
- MCP tests: open the client session *inside* each test (anyio cancel
  scopes break across pytest-asyncio fixture teardown).
- Logging, not print (CLI summaries excepted). Never hardcode or log keys.

## Gotchas

- Deterministic Czech-keyword validation (`classification_rules.py`) was
  **removed 2026-07**: zero confirmed LLM errors caught, one false
  positive (`kukuřičné tortilly` flagged as gluten), tables hard-coupled
  to Czech. Don't reintroduce ad hoc — the language-neutral plan is the
  Reliability roadmap above. Substring traps for any future ruleset:
  `kokosové mléko` ≠ dairy, `muškátový oříšek` ≠ nuts, `arašídové máslo`
  ≠ butter, `cukr krupice` (sugar) ≠ `krupice` (semolina), `rybíz` ≠
  `ryb` (fish).
- Recipe IDs are non-contiguous (gaps exist) — never assume sequential ids.
- Chat sessions are in-memory only (lost on restart) — intentional for
  the MVP; durable stores implement `SessionStore`.
- pydantic-ai 2.x: MCP client is `pydantic_ai.mcp.MCPToolset` (no
  `MCPServerStdio`); accepts the official-SDK `FastMCP` instance directly.
  `result.usage` is a property; `history_processors` no longer exists.
- Hidden ingredients (stock in a paste, gluten in a spice mix) cannot be
  detected from ingredient names — classifications stay approximate until
  quantity/nutrition data exists.
