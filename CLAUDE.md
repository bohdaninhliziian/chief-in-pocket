# Chef in My Pocket — MVP

Czech recipe assistant: import → LLM enrichment (offline) → deterministic
runtime layer exposed over MCP → conversational agent + chat UI. Backend
in `backend/` (uv-managed, Python ≥ 3.11, Pydantic v2, Pydantic AI,
official `mcp` SDK); frontend in `frontend/` (Vite + React 19 + TS strict,
desktop-only chat page).

Docs map: root `README.md` — short entry point for the case-study
reviewer (keep in sync); `Doc.md` — system-design deliverable (Mermaid
architecture diagrams + framework/model justifications + limitations);
`backend/CLAUDE.md` — the backend technical deep-dive (module layout,
importer/enrichment/MCP/chat details, reliability roadmap, gotchas —
it replaced `backend/README.md`).

> **Keep this file current.** Whenever an important change happens —
> new module, changed architecture, new CLI flag, changed conventions,
> bumped prompt/classifier version, new gotcha discovered — update
> CLAUDE.md (root and/or backend) in the same session. Stale guidance is
> worse than none.

## Architecture (the one rule that matters)

```
PHASE 1 offline: data/raw/recipes.csv → scripts/import_recipes.py → data/processed/recipes.json
PHASE 2 offline: recipes.json → scripts/enrich_recipes_llm.py (LLM) → recipes_enriched.json
PHASE 3 runtime: recipes_enriched.json → repository → services → MCP server (NO LLM)
PHASE 4 runtime: chat API → Pydantic AI agent (the ONLY runtime LLM) → in-process
                 MCP business tools → deterministic services → session store
```

The classification LLM is called **only** in phase 2. Runtime search is
deterministic filtering over pre-classified data — never add LLM calls,
semantic search or network I/O to the deterministic runtime path
(repositories/services/mcp_server). The phase-4 chat agent is the single
runtime LLM: it interprets intent and picks a business MCP tool; all
state changes run in deterministic Python (`MealPlanService` + validated
`SessionState`). The LLM never mutates the session store directly, and
`session_id` on business tools is force-injected by a `process_tool_call`
hook — the model can never target another session.

Backend module layout, per-phase details (importer CSV quirks, enrichment
caching/versioning, MCP tool contracts, chat/voice design) and the
hard-won gotchas live in **`backend/CLAUDE.md`** — read it before
touching backend code.

## Frontend (`frontend/`)

Single desktop-only chat page: conversation left, meal plan + shopping
list right. Thin view over backend state — renders `meal_plan` from the
chat response verbatim, never reconstructs it. Voice in = mic button →
POST /transcribe → transcript into the input (never auto-sent). Voice
out = per-assistant-message speaker button → `voice_summary` → POST
/speak → cached audio blob per message (replays are free, no refetch).
Session id in `localStorage`. Conventions in the **/frontend-standards**
skill; review with **frontend-code-reviewer**. Gates (from `frontend/`):
`npm run typecheck` · `npm test` · `npm run build`.

## Quality gates (run from `backend/`, all three before claiming done)

```bash
uv run ruff check .    # lint: E,F,I,UP,B,SIM,RUF · py311 · line 100
uv run mypy src        # types: pydantic plugin, disallow_untyped_defs
uv run pytest -q       # full suite, all offline
```

## Project agents & skills

Agents (`.claude/agents/`): **recipe-code-reviewer** (run after backend
changes — enforces the invariants in these files + gates),
**frontend-code-reviewer** (run after frontend changes — conventions +
frontend gates), **dataset-auditor** (run after enrichment or prompt
changes — sanity-checks stored classifications, read-only),
**mcp-e2e-tester** (run before shipping MCP changes — real stdio
subprocess, all tools + error paths).

Skills (`.claude/skills/`): **/enrich** (safe paid-classification
procedure), **/add-recipes** (import → enrich → audit → docs),
**/new-mcp-tool** (service-first checklist), **/bump-classifier**
(version bumps + cache invalidation), **/python-standards** (coding
conventions — load when writing Python here), **/frontend-standards**
(load when writing code in `frontend/`).

## Commands (run from `backend/`)

```bash
uv sync                                   # install deps
uv run pytest -q                          # full suite (all offline)
uv run python scripts/import_recipes.py            # CSV import (default --limit 20)
uv run python scripts/enrich_recipes_llm.py --dry-run   # what would be classified
uv run python scripts/enrich_recipes_llm.py --limit 5 --pretty  # paid LLM calls!
uv run python scripts/run_mcp_server.py             # MCP server on stdio
npx @modelcontextprotocol/inspector uv run python scripts/run_mcp_server.py
uv run python scripts/run_chat_api.py               # chat API (needs OPENAI_API_KEY)
uv run python scripts/chat_demo.py                  # CLI client for the chat API
```

Frontend (run from `frontend/`): `npm install` · `npm run dev` (Vite on
:5173, expects the chat API on :8000) · `npm run typecheck` · `npm test` ·
`npm run build`.

Secrets: `OPENAI_API_KEY` (chat agent) and `ELEVENLABS_API_KEY` (voice —
STT + TTS; without it /transcribe and /speak return 503). Optional:
`RECIPE_CLASSIFIER_MODEL` and `CHAT_AGENT_MODEL` (both default
`openai:gpt-5-mini`), `CHAT_MODEL_TIMEOUT_SECONDS`,
`CHAT_HISTORY_MAX_MESSAGES`, `CHAT_STT_LANGUAGE` (default `cs`),
`ELEVENLABS_STT_MODEL`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_TTS_MODEL`.
All come from env or `backend/.env` (gitignored; template in
`.env.example`). Never hardcode or log keys.

## Cross-cutting conventions

- **Enrichment costs money.** Never run `enrich_recipes_llm.py` over the
  full dataset unprompted; use `--dry-run` or `--limit N`. Caching skips
  already-classified recipes; `--force` reclassifies.
- **Closed enums everywhere.** Dietary goals are exactly the 7 in
  `DietaryGoal`; classifier and MCP schemas both enforce them.
- Every JSON write uses `ensure_ascii=False` (Czech text must stay readable).
- Tests must never call a real LLM/provider (enforced by
  `ALLOW_MODEL_REQUESTS = False` in conftest).
- Exceptions are for real errors only; zero search matches returns `[]`.
- After changing the classifier prompt, bump `CLASSIFIER_PROMPT_VERSION`.
- Logging, not print (CLI summaries are the exception).

## Status

The full dataset is imported and enriched (run the **dataset-auditor**
agent or check `data/processed/llm_enrichment_report.json` for current
counts/confidence). Built: deterministic runtime layer + MCP server (8 tools),
conversational agent (single Pydantic AI agent over in-process MCP
business tools + FastAPI POST /chat), desktop chat frontend with voice
in/out (ElevenLabs-only by design, swappable behind
`SpeechTranscriber`/`SpeechSynthesizer` protocols). Plans are generic
"Day 1..N" (1–31 days, 1–3 meals/day). System-design deliverable written
(`Doc.md`: Mermaid diagrams + technical choices + limitations).
2026-07: deterministic Czech-keyword validation removed from enrichment
(LLM-only + confidence gate; language-neutral replacement planned — see
"Reliability roadmap" in `backend/CLAUDE.md`); `backend/README.md`
removed, content merged into `backend/CLAUDE.md`. Known issue: Czech STT
accuracy is poor even with the pinned language. Not yet built:
PostgreSQL/Redis (slot in behind `RecipeRepository`/`SessionStore`
protocols), auth, streaming responses, per-locale classification
validation.
