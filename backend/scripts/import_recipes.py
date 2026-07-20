#!/usr/bin/env python3
"""CLI entry point for the recipe CSV → JSON importer.

Usage:
    python backend/scripts/import_recipes.py \
        --input backend/data/raw/recipes.csv \
        --output backend/data/processed/recipes.json \
        --start-id 10 --end-id 33 --limit 20 --pretty
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

from recipes.importer import import_recipes

DEFAULT_INPUT = BACKEND_DIR / "data" / "raw" / "recipes.csv"
DEFAULT_OUTPUT = BACKEND_DIR / "data" / "processed" / "recipes.json"
# MVP default: import only the first 20 recipes. Pass --limit 0 for no cap.
DEFAULT_LIMIT = 20

logger = logging.getLogger("import_recipes")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import recipes from a raw CSV into a normalized JSON file."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Path to the source CSV (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Path of the generated JSON file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--start-id",
        type=int,
        default=None,
        help="Only import recipes with id >= START_ID.",
    )
    parser.add_argument(
        "--end-id",
        type=int,
        default=None,
        help="Only import recipes with id <= END_ID.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=(
            "Import at most N valid recipes "
            f"(default: {DEFAULT_LIMIT}; use 0 for no limit)."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Write indented, human-readable JSON.",
    )
    parser.add_argument(
        "--fail-on-invalid",
        action="store_true",
        help="Exit with a non-zero status if any row fails validation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)

    if not args.input.is_file():
        logger.error("Input CSV not found: %s", args.input)
        return 2

    summary = import_recipes(
        input_path=args.input,
        output_path=args.output,
        start_id=args.start_id,
        end_id=args.end_id,
        limit=args.limit if args.limit and args.limit > 0 else None,
        pretty=args.pretty,
    )

    print(summary.format())

    if args.fail_on_invalid and summary.skipped:
        logger.error("%d row(s) failed validation (--fail-on-invalid).", summary.skipped)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
