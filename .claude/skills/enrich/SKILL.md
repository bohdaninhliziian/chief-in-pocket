---
name: enrich
description: Safe procedure for running the paid LLM enrichment pipeline (recipe classification). Use whenever recipes need to be classified or reclassified — it costs real API money, so dry-run first, scope explicitly, audit after.
---

# Enrich recipes (paid LLM calls — follow in order)

All commands run from `backend/`. Requires `OPENAI_API_KEY` (env or `.env`).

## 1. Dry-run first, always

```bash
uv run python scripts/enrich_recipes_llm.py --dry-run
```

Report the eligible (non-cached) count to the user. The fingerprint cache
(id + name + ingredients + model + versions) skips already-classified
recipes automatically — 0 eligible means nothing to do.

## 2. Estimate cost before running

~2,500 tokens per recipe on `openai:gpt-5-mini` (output-heavy: reasoning
tokens). 20 recipes ≈ 50k tokens ≈ a few cents. State the estimate.

## 3. Scope explicitly

- If the user already specified scope (a count, "all", specific range), use it.
- Otherwise ask before any paid run.
- Prefer `--limit N` for samples. Never `--force` (full reclassify, ignores
  cache) unless the user explicitly asked for it.

```bash
uv run python scripts/enrich_recipes_llm.py --limit 5 --pretty   # sample
uv run python scripts/enrich_recipes_llm.py --pretty             # all eligible
```

## 4. Known failure modes

- `insufficient_quota` (HTTP 429) = **billing**, not rate limiting. The CLI
  fails fast by design. Tell the user to check credits/project on
  platform.openai.com; do not retry in a loop.
- 401/403 = key problem; check `.env` vs exported env.
- Failed recipes are excluded from output and retried on the next run.

## 5. Audit the result

Launch the `dataset-auditor` agent after every run that classified ≥ 1
recipe. Report to the user: summary counts, validation warnings,
needs-review items, and anything the auditor flags.

## 6. Record

If the dataset scope changed (new recipes enriched), update the **Status**
section of `CLAUDE.md`.
