#!/usr/bin/env python3
"""CLI for the offline LLM recipe enrichment pipeline.

Usage:
    export OPENAI_API_KEY=...
    python backend/scripts/enrich_recipes_llm.py \
        --input backend/data/processed/recipes.json \
        --output backend/data/processed/recipes_enriched.json \
        --report backend/data/processed/llm_enrichment_report.json \
        --model openai:gpt-5-mini --concurrency 3 --limit 5 --pretty
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

from dotenv import load_dotenv

# Load OPENAI_API_KEY / RECIPE_CLASSIFIER_MODEL from backend/.env when
# present; real environment variables always take precedence.
load_dotenv(BACKEND_DIR / ".env")

from recipes.classifier import (
    ClassificationError,
    PydanticAiRecipeClassifier,
    require_api_key,
    resolve_model_name,
)
from recipes.enrichment import (
    EnrichmentOptions,
    enrich_recipes,
)

DEFAULT_INPUT = BACKEND_DIR / "data" / "processed" / "recipes.json"
DEFAULT_OUTPUT = BACKEND_DIR / "data" / "processed" / "recipes_enriched.json"
DEFAULT_REPORT = BACKEND_DIR / "data" / "processed" / "llm_enrichment_report.json"

logger = logging.getLogger("enrich_recipes_llm")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify recipes offline with an LLM (dietary goals, "
        "allergens, meal type) and store enriched JSON."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--model",
        default=None,
        help="Model name, e.g. openai:gpt-5-mini "
        "(default: $RECIPE_CLASSIFIER_MODEL or openai:gpt-5-mini)",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None,
                        help="Classify at most N eligible (non-cached) recipes.")
    parser.add_argument("--force", action="store_true",
                        help="Ignore cached classifications and reclassify everything.")
    parser.add_argument("--resume", action="store_true",
                        help="Reuse prior output and continue unfinished work "
                        "(this is also the default behavior).")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be classified; no API calls.")
    parser.add_argument("--max-retries", type=int, default=3,
                        help="Bounded retries for transient provider failures.")
    parser.add_argument("--confidence-threshold", type=float, default=0.70,
                        help="Classifications below this become needs-review.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit non-zero when any recipe fails.")
    return parser


async def run(args: argparse.Namespace) -> int:
    model_name = resolve_model_name(args.model)
    options = EnrichmentOptions(
        model_name=model_name,
        concurrency=args.concurrency,
        limit=args.limit,
        force=args.force,
        dry_run=args.dry_run,
        confidence_threshold=args.confidence_threshold,
        strict=args.strict,
        pretty=args.pretty,
    )

    classifier = None
    if not args.dry_run:
        require_api_key()
        classifier = PydanticAiRecipeClassifier(
            model_name, max_retries=args.max_retries
        )

    report = await enrich_recipes(
        input_path=args.input,
        output_path=args.output,
        report_path=args.report,
        classifier=classifier,
        options=options,
    )

    if args.dry_run:
        print(
            f"Dry run: {report.eligible} recipe(s) would be classified with "
            f"{model_name}; {report.skipped_from_cache} already cached."
        )
        return 0

    print(report.format_summary())
    if args.strict and report.failed:
        logger.error("%d recipe(s) failed and --strict is enabled.", report.failed)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if not args.input.is_file():
        logger.error("Input file not found: %s", args.input)
        return 2
    try:
        return asyncio.run(run(args))
    except ClassificationError as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
