"""CSV → JSON recipe importer.

Reads the raw recipe CSV, normalizes and validates each row into a
:class:`recipes.models.Recipe`, and writes the result as a JSON array.

The source CSV has the header ``id,name,name,author_note,ingredients,steps``
— the second ``name`` column holds the author. Because of the duplicate
header name, rows are parsed positionally with ``csv.reader`` rather than
``csv.DictReader``.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from recipes.models import Recipe
from recipes.normalization import (
    clean_description,
    normalize_text,
    split_ingredients,
    split_instructions,
    strip_html,
)

logger = logging.getLogger(__name__)

EXPECTED_COLUMNS = 6
_COL_ID, _COL_NAME, _COL_AUTHOR, _COL_DESCRIPTION, _COL_INGREDIENTS, _COL_STEPS = range(
    EXPECTED_COLUMNS
)


@dataclass
class ImportSummary:
    """Outcome of a single import run."""

    rows_read: int = 0
    imported: int = 0
    skipped: int = 0
    filtered_out: int = 0
    warnings: list[str] = field(default_factory=list)
    output_path: Path | None = None

    def format(self) -> str:
        lines = [
            f"Rows read: {self.rows_read}",
            f"Recipes imported: {self.imported}",
            f"Rows skipped: {self.skipped}",
        ]
        if self.filtered_out:
            lines.append(f"Rows outside id range / over limit: {self.filtered_out}")
        lines.append(f"Warnings: {len(self.warnings)}")
        if self.output_path is not None:
            lines.append(f"Output: {self.output_path}")
        return "\n".join(lines)


def parse_row(row: list[str]) -> Recipe:
    """Convert one raw CSV row into a validated :class:`Recipe`.

    Raises ``ValueError`` for malformed rows and ``pydantic.ValidationError``
    for rows that fail model validation.
    """
    if len(row) != EXPECTED_COLUMNS:
        raise ValueError(
            f"expected {EXPECTED_COLUMNS} columns, got {len(row)}"
        )

    raw_id = row[_COL_ID].strip()
    if not raw_id.lstrip("-").isdigit():
        raise ValueError(f"id is not an integer: {raw_id!r}")

    author = normalize_text(row[_COL_AUTHOR]) or None
    return Recipe(
        id=int(raw_id),
        name=normalize_text(strip_html(row[_COL_NAME])),
        author=author,
        description=clean_description(row[_COL_DESCRIPTION]),
        ingredients=split_ingredients(row[_COL_INGREDIENTS]),
        instructions=split_instructions(row[_COL_STEPS]),
    )


def import_recipes(
    input_path: Path,
    output_path: Path,
    *,
    start_id: int | None = None,
    end_id: int | None = None,
    limit: int | None = None,
    pretty: bool = False,
) -> ImportSummary:
    """Import recipes from ``input_path`` and write them to ``output_path``.

    Invalid rows are skipped with a logged warning; they never abort the run.
    ``start_id``/``end_id`` select an inclusive recipe-id range, ``limit``
    caps the number of imported recipes.
    """
    summary = ImportSummary()
    recipes: list[Recipe] = []

    with input_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            line_number = reader.line_num
            if line_number == 1 and row and row[0].strip().lower() == "id":
                continue  # header
            if not row or not any(cell.strip() for cell in row):
                continue  # blank line
            summary.rows_read += 1

            if limit is not None and len(recipes) >= limit:
                summary.filtered_out += 1
                continue

            try:
                recipe = parse_row(row)
            except (ValueError, ValidationError) as exc:
                message = f"row {line_number}: skipped ({_short_error(exc)})"
                logger.warning(message)
                summary.warnings.append(message)
                summary.skipped += 1
                continue

            if (start_id is not None and recipe.id < start_id) or (
                end_id is not None and recipe.id > end_id
            ):
                summary.filtered_out += 1
                continue

            recipes.append(recipe)

    write_recipes_json(recipes, output_path, pretty=pretty)
    summary.imported = len(recipes)
    summary.output_path = output_path
    return summary


def write_recipes_json(
    recipes: list[Recipe], output_path: Path, *, pretty: bool = False
) -> None:
    """Serialize recipes as a JSON array with readable UTF-8 characters."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [recipe.model_dump() for recipe in recipes]
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2 if pretty else None,
        )
        handle.write("\n")


def _short_error(exc: Exception) -> str:
    """One-line, traceback-free reason for a validation failure."""
    if isinstance(exc, ValidationError):
        reasons = "; ".join(
            f"{'.'.join(str(loc) for loc in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return reasons or "validation failed"
    return str(exc)
