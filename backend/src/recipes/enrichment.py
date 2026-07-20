"""Offline LLM enrichment pipeline: cache, concurrency, report.

The pipeline classifies recipes with a :class:`recipes.classifier
.RecipeClassifier`, gates low-confidence results into ``needs-review``,
and writes an enriched JSON file incrementally (atomic replace after every
finished recipe), so an interrupted run never loses completed work and can
resume.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from recipes.classification import (
    Allergen,
    ClassificationMetadata,
    DietaryGoal,
    EnrichedRecipe,
    MealType,
    ReviewStatus,
)
from recipes.classifier import ClassificationError, RecipeClassifier
from recipes.models import Recipe
from recipes.recipe_classifier_prompt import (
    CLASSIFIER_PROMPT_VERSION,
    CLASSIFIER_VERSION,
)

logger = logging.getLogger(__name__)


def compute_fingerprint(
    recipe: Recipe,
    model_name: str,
    classifier_version: str = CLASSIFIER_VERSION,
    prompt_version: str = CLASSIFIER_PROMPT_VERSION,
) -> str:
    """Stable cache key for one recipe + classifier configuration.

    Any change to the recipe name, its ingredients, the model or either
    version string produces a different fingerprint and forces
    reclassification.
    """
    payload = json.dumps(
        {
            "id": recipe.id,
            "name": recipe.name.casefold().strip(),
            "ingredients": [i.casefold().strip() for i in recipe.ingredients],
            "model": model_name,
            "classifier_version": classifier_version,
            "prompt_version": prompt_version,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class UsageReport(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class EnrichmentReport(BaseModel):
    """Machine-readable summary of one enrichment run."""

    total_recipes: int = 0
    eligible: int = 0
    classified: int = 0
    skipped_from_cache: int = 0
    accepted: int = 0
    needs_review: int = 0
    failed: int = 0
    goal_counts: dict[str, int] = Field(default_factory=dict)
    allergen_counts: dict[str, int] = Field(default_factory=dict)
    meal_type_counts: dict[str, int] = Field(default_factory=dict)
    average_confidence: float | None = None
    model: str | None = None
    classifier_version: str = CLASSIFIER_VERSION
    prompt_version: str = CLASSIFIER_PROMPT_VERSION
    duration_seconds: float = 0.0
    usage: UsageReport = Field(default_factory=UsageReport)
    estimated_cost_usd: float | None = None
    errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)

    def format_summary(self) -> str:
        lines = [
            f"Recipes loaded: {self.total_recipes}",
            f"Classified: {self.classified}",
            f"Skipped from cache: {self.skipped_from_cache}",
            f"Needs review: {self.needs_review}",
            f"Failed: {self.failed}",
            "",
            "Supported goal counts:",
        ]
        for goal in DietaryGoal:
            lines.append(f"  {goal.value}: {self.goal_counts.get(goal.value, 0)}")
        if self.average_confidence is not None:
            lines.append("")
            lines.append(f"Average confidence: {self.average_confidence:.2f}")
        return "\n".join(lines)


@dataclass
class EnrichmentOptions:
    model_name: str
    concurrency: int = 3
    limit: int | None = None
    force: bool = False
    dry_run: bool = False
    confidence_threshold: float = 0.70
    strict: bool = False
    pretty: bool = False


def load_recipes(input_path: Path) -> list[Recipe]:
    with input_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return [Recipe.model_validate(item) for item in raw]


def load_existing_enriched(output_path: Path) -> dict[str, EnrichedRecipe]:
    """Previously enriched recipes keyed by fingerprint (for cache/resume)."""
    if not output_path.is_file():
        return {}
    try:
        with output_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        records = [EnrichedRecipe.model_validate(item) for item in raw]
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "existing output %s is not readable (%s); starting fresh",
            output_path,
            exc,
        )
        return {}
    return {record.classification.fingerprint: record for record in records}


def write_json_atomic(path: Path, payload: object, *, pretty: bool) -> None:
    """Write JSON via a temp file + rename so readers never see partial data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2 if pretty else None)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _estimate_cost(usage: UsageReport, model_name: str) -> float | None:
    """Best-effort price estimate via genai-prices; None when unavailable."""
    try:
        from genai_prices import calc_price

        model_ref = model_name.split(":", 1)[-1]
        price = calc_price(
            # UsageReport structurally satisfies AbstractUsage
            # (input_tokens/output_tokens), but only duck-typed.
            usage,  # type: ignore[call-overload]
            model_ref=model_ref,
        )
        return float(price.total_price)
    except Exception:
        return None


async def _classify_recipe(
    recipe: Recipe,
    classifier: RecipeClassifier,
    options: EnrichmentOptions,
    fingerprint: str,
) -> EnrichedRecipe:
    """Classify one recipe and gate low-confidence results into review."""
    classification = await classifier.classify(recipe)

    warnings: list[str] = []
    if classification.confidence < options.confidence_threshold:
        review_status = ReviewStatus.NEEDS_REVIEW
        warnings.append(
            f"confidence {classification.confidence:.2f} is below the "
            f"threshold {options.confidence_threshold:.2f}"
        )
    else:
        review_status = ReviewStatus.ACCEPTED

    metadata = ClassificationMetadata(
        source="llm",
        model=options.model_name,
        classifier_version=CLASSIFIER_VERSION,
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        classified_at=datetime.now(UTC),
        confidence=classification.confidence,
        review_status=review_status,
        fingerprint=fingerprint,
        validation_warnings=warnings,
    )
    return EnrichedRecipe.from_parts(recipe, classification, metadata)


async def enrich_recipes(
    input_path: Path,
    output_path: Path,
    report_path: Path,
    classifier: RecipeClassifier | None,
    options: EnrichmentOptions,
) -> EnrichmentReport:
    """Run the enrichment pipeline end to end and write output + report.

    ``classifier`` may be None only for ``--dry-run``. One failed recipe
    never aborts the batch; failures are recorded in the report (and, with
    ``strict``, reflected in the CLI exit code).
    """
    started = time.monotonic()
    report = EnrichmentReport(model=options.model_name)

    recipes = load_recipes(input_path)
    report.total_recipes = len(recipes)

    cache = {} if options.force else load_existing_enriched(output_path)

    results: dict[int, EnrichedRecipe] = {}
    to_classify: list[tuple[Recipe, str]] = []
    for recipe in recipes:
        fingerprint = compute_fingerprint(recipe, options.model_name)
        cached = cache.get(fingerprint)
        if cached is not None and cached.classification.review_status is not ReviewStatus.FAILED:
            results[recipe.id] = cached
            report.skipped_from_cache += 1
        else:
            to_classify.append((recipe, fingerprint))

    if options.limit is not None:
        to_classify = to_classify[: options.limit]
    report.eligible = len(to_classify)

    if options.dry_run:
        for recipe, fingerprint in to_classify:
            logger.info(
                "dry-run: would classify recipe %s (%s), fingerprint %s",
                recipe.id,
                recipe.name,
                fingerprint[:12],
            )
        report.duration_seconds = round(time.monotonic() - started, 2)
        return report

    if classifier is None:
        raise ValueError("a classifier is required unless dry_run is enabled")

    semaphore = asyncio.Semaphore(max(1, options.concurrency))
    write_lock = asyncio.Lock()

    async def flush_output() -> None:
        ordered = [results[key] for key in sorted(results)]
        payload = [record.model_dump(mode="json") for record in ordered]
        write_json_atomic(output_path, payload, pretty=options.pretty)

    async def process(recipe: Recipe, fingerprint: str) -> None:
        async with semaphore:
            try:
                enriched = await _classify_recipe(recipe, classifier, options, fingerprint)
            except ClassificationError as exc:
                report.failed += 1
                report.errors.append(f"recipe {recipe.id} ({recipe.name}): {exc}")
                logger.error("recipe %s failed: %s", recipe.id, exc)
                return

        async with write_lock:
            results[recipe.id] = enriched
            report.classified += 1
            for warning in enriched.classification.validation_warnings:
                report.validation_warnings.append(f"recipe {recipe.id}: {warning}")
            await flush_output()

    await asyncio.gather(
        *(process(recipe, fingerprint) for recipe, fingerprint in to_classify)
    )

    # Final write also covers the cache-only case (no new classifications).
    async with write_lock:
        await flush_output()

    _finalize_report(report, list(results.values()), options, started, classifier)
    write_json_atomic(report_path, report.model_dump(mode="json"), pretty=True)
    return report


def _finalize_report(
    report: EnrichmentReport,
    records: list[EnrichedRecipe],
    options: EnrichmentOptions,
    started: float,
    classifier: RecipeClassifier | None = None,
) -> None:
    goal_counter: Counter[str] = Counter()
    allergen_counter: Counter[str] = Counter()
    meal_counter: Counter[str] = Counter()
    confidences: list[float] = []
    for record in records:
        goal_counter.update(goal.value for goal in record.supported_goals)
        allergen_counter.update(a.value for a in record.allergens)
        if record.meal_type is not None:
            meal_counter[record.meal_type.value] += 1
        confidences.append(record.classification.confidence)
        if record.classification.review_status is ReviewStatus.NEEDS_REVIEW:
            report.needs_review += 1
        elif record.classification.review_status is ReviewStatus.ACCEPTED:
            report.accepted += 1

    report.goal_counts = {goal.value: goal_counter.get(goal.value, 0) for goal in DietaryGoal}
    report.allergen_counts = {a.value: allergen_counter.get(a.value, 0) for a in Allergen}
    report.meal_type_counts = {m.value: meal_counter.get(m.value, 0) for m in MealType}
    if confidences:
        report.average_confidence = round(sum(confidences) / len(confidences), 4)
    report.duration_seconds = round(time.monotonic() - started, 2)

    usage = getattr(classifier, "total_usage", None)
    if usage is not None:
        report.usage = UsageReport(
            requests=usage.requests,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            total_tokens=usage.total_tokens or 0,
        )
        report.estimated_cost_usd = _estimate_cost(report.usage, options.model_name)
