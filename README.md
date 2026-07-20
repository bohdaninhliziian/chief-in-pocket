# Chef in My Pocket

A conversational meal-planning assistant over 100 Czech recipes. Chat
with it (text or voice) to build a multi-day meal plan for a dietary
goal, swap individual meals, exclude ingredients, and get a merged
shopping list next to the conversation.

## How it works

```
offline  data/raw/recipes.csv ──import──▶ recipes.json ──LLM enrichment──▶ recipes_enriched.json
runtime  chat UI / API ──▶ Pydantic AI agent ──▶ MCP business tools ──▶ deterministic
                            (the only runtime LLM)                     services + session state
```

The design rule: **LLMs classify offline, Python decides at runtime.**
Recipes are classified once, offline, by an LLM; at runtime a single
Pydantic AI agent picks an MCP tool and every state change runs as
deterministic, validated Python. Voice is ElevenLabs STT/TTS behind the
same chat flow.

The full architecture (diagrams, framework and model choices) is in
[Doc.md](Doc.md); the technical deep-dive (design decisions, API
reference, roadmap) is in [backend/CLAUDE.md](backend/CLAUDE.md).

## Where to find the components

| Component | Location |
|---|---|
| Conversational agent (Pydantic AI) | `backend/src/recipes/chat/` |
| MCP server + tools | `backend/src/recipes/mcp_server/` |
| Data processing scripts | `backend/scripts/` — `import_recipes.py` (CSV → JSON), `enrich_recipes_llm.py` (LLM classification) |

## Data pipeline (import + enrichment)

The raw dataset lives at **`backend/data/raw/recipes.csv`** — a
replacement or extended CSV goes to that exact path (same columns:
`id,name,name,author_note,ingredients,steps`). Processing has two
offline steps, run from `backend/` in this order:

```bash
# 1 — import: CSV → normalized JSON (offline, free)
uv run python scripts/import_recipes.py --limit 0    # --limit 0 = all rows

# 2 — enrichment: classify recipes with an LLM (paid — needs OPENAI_API_KEY)
uv run python scripts/enrich_recipes_llm.py --dry-run   # preview what would be classified
uv run python scripts/enrich_recipes_llm.py             # run the classification
```

Enrichment caches by content fingerprint and resumes safely: already
classified recipes are skipped, so re-runs only pay for new or changed
ones (`--force` reclassifies everything). **The enriched output for all
100 recipes is committed** (`backend/data/processed/recipes_enriched.json`),
so you only need these steps after changing the CSV — not to run the app.

## Running it

Prerequisites: Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node 18+.

```bash
# backend (from backend/)
uv sync
cp .env.example .env        # add the API keys, see below
uv run python scripts/run_chat_api.py     # chat API on :8000

# frontend (from frontend/, second terminal)
npm install
npm run dev                                # chat UI on http://localhost:5173
```

API keys (in `backend/.env`):

- `OPENAI_API_KEY` — required for the chat agent (and for enrichment).
- `ELEVENLABS_API_KEY` — optional, enables voice. The key must have
  access to **both Speech-to-Text (Scribe) and Text-to-Speech** — mic
  input uses STT, the per-message speaker button uses TTS. Without the
  key the app still works; `/transcribe` and `/speak` return 503.
