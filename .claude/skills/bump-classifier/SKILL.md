---
name: bump-classifier
description: Procedure for changing the recipe classifier - prompt edits, model swaps. Ensures version bumps, cache invalidation awareness, and a sample-first re-enrichment. Use before touching recipe_classifier_prompt.py or swapping the classifier model.
---

# Change the classifier safely

Classification results are cached by fingerprint =
(id, name, ingredients, model, `CLASSIFIER_VERSION`, `CLASSIFIER_PROMPT_VERSION`).
Version bumps are how you invalidate the cache — silent prompt edits would
leave stale classifications live.

## 1. Make the change

- Prompt/goal definitions → `src/recipes/recipe_classifier_prompt.py`
- Classification is LLM-only (no deterministic keyword rules — removed
  2026-07; see backend/CLAUDE.md "Reliability roadmap" before considering
  reintroducing validation). Quality levers are the prompt, the model and
  the confidence threshold.

## 2. Bump the right version

In `recipe_classifier_prompt.py`:
- prompt wording/definitions changed → bump `CLASSIFIER_PROMPT_VERSION`
- classification logic/schema semantics changed → bump `CLASSIFIER_VERSION`

## 3. Confirm invalidation scope

```bash
cd backend && uv run python scripts/enrich_recipes_llm.py --dry-run
```

Eligible count should equal the recipes affected by the bump (all of them,
after a version bump). State the count + cost estimate (~2.5k tokens/recipe).

## 4. Sample before full run

Re-enrich a small sample and diff against the previous output:

```bash
cp data/processed/recipes_enriched.json /tmp/enriched_before.json
uv run python scripts/enrich_recipes_llm.py --limit 3 --pretty
```

Compare goals/allergens/meal types for the 3 recipes. Explain every
difference — is it the intended improvement or a regression?

## 5. Full run only on approval

Full re-enrichment costs money — get explicit user approval, then run
without `--limit` and launch the `dataset-auditor` agent on the result.

## 6. Record

Run the gates (`ruff` / `mypy` / `pytest`) and note the version bump +
reason in `CLAUDE.md`.
