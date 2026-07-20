---
name: add-recipes
description: End-to-end procedure for extending the recipe dataset - import new recipes from the CSV, enrich them, audit, and update docs. Use when the user wants more recipes available to the runtime/MCP layer.
---

# Add recipes to the dataset

All commands run from `backend/`.

## 1. Determine the delta

```bash
uv run python -c "
import csv, json
csv_ids = sorted(int(r[0]) for r in list(csv.reader(open('data/raw/recipes.csv', encoding='utf-8')))[1:] if r and r[0].strip().isdigit())
done_ids = sorted(r['id'] for r in json.load(open('data/processed/recipes.json', encoding='utf-8')))
print('CSV range:', csv_ids[0], '-', csv_ids[-1], f'({len(csv_ids)} recipes)')
print('imported :', done_ids[0], '-', done_ids[-1], f'({len(done_ids)} recipes)')
print('missing  :', [i for i in csv_ids if i not in set(done_ids)][:30])
"
```

IDs are non-contiguous (10–113 with gaps) — always work from actual IDs,
never assume ranges.

## 2. Import the new slice

Never import everything at once unless the user asked. The importer appends
nothing — it rewrites the output — so include the already-imported range:

```bash
uv run python scripts/import_recipes.py --start-id <low> --end-id <high> --limit 0 --pretty
```

(`--limit 0` = no cap; the id range does the scoping.)

## 3. Spot-check the imported JSON

For 2–3 new recipes verify: no HTML remnants (`grep -c '<' …` should hit
nothing in text fields), instructions split into sensible steps (not one
blob, no tiny fragments), Czech diacritics intact, ingredients deduplicated.

## 4. Enrich the delta

Invoke the `enrich` skill — its cache automatically classifies only the new
recipes. Costs money; follow that skill's dry-run/confirm steps.

## 5. Verify runtime picks it up

```bash
uv run pytest -q          # must stay green
```

Optionally run the `mcp-e2e-tester` agent if the MCP server serves the new data.

## 6. Update docs

Update the **Status** section of `CLAUDE.md` (imported range, enriched count).
