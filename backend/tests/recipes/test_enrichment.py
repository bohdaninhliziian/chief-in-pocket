"""Tests for fingerprints, caching/resume, batch behavior and reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import (
    FISH_RECIPE,
    GULAS,
    NUT_CAKE,
    FakeClassifier,
    make_classification,
    make_recipe,
)
from pydantic_ai.models.test import TestModel

from recipes.classification import (
    Allergen,
    DietaryGoal,
    MealType,
)
from recipes.classifier import ClassificationError, PydanticAiRecipeClassifier
from recipes.enrichment import (
    EnrichmentOptions,
    compute_fingerprint,
    enrich_recipes,
)
from recipes.models import Recipe

MODEL = "openai:gpt-5-mini"


def write_input(path: Path, recipes: list[Recipe]) -> Path:
    path.write_text(
        json.dumps([r.model_dump() for r in recipes], ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "input": tmp_path / "recipes.json",
        "output": tmp_path / "recipes_enriched.json",
        "report": tmp_path / "report.json",
    }


def options(**kwargs: object) -> EnrichmentOptions:
    return EnrichmentOptions(model_name=MODEL, **kwargs)  # type: ignore[arg-type]


class TestFingerprint:
    def test_stable_for_identical_input(self) -> None:
        assert compute_fingerprint(GULAS, MODEL) == compute_fingerprint(GULAS, MODEL)

    def test_changed_ingredients_invalidate(self) -> None:
        changed = GULAS.model_copy(
            update={"ingredients": [*GULAS.ingredients, "Chilli"]}
        )
        assert compute_fingerprint(GULAS, MODEL) != compute_fingerprint(changed, MODEL)

    def test_changed_prompt_version_invalidates(self) -> None:
        assert compute_fingerprint(GULAS, MODEL) != compute_fingerprint(
            GULAS, MODEL, prompt_version="2"
        )

    def test_changed_classifier_version_invalidates(self) -> None:
        assert compute_fingerprint(GULAS, MODEL) != compute_fingerprint(
            GULAS, MODEL, classifier_version="2"
        )

    def test_changed_model_invalidates(self) -> None:
        assert compute_fingerprint(GULAS, MODEL) != compute_fingerprint(
            GULAS, "openai:gpt-5"
        )


class TestPipeline:
    async def test_enriched_output_preserves_recipe_and_provenance(
        self, paths: dict[str, Path]
    ) -> None:
        write_input(paths["input"], [GULAS])
        classifier = FakeClassifier(
            default=make_classification(
                goals=[DietaryGoal.HIGH_PROTEIN],
                allergens=[Allergen.GLUTEN, Allergen.MILK, Allergen.EGGS],
                confidence=0.94,
            )
        )
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], classifier, options()
        )

        assert report.classified == 1
        data = json.loads(paths["output"].read_text(encoding="utf-8"))
        record = data[0]
        # Original fields preserved verbatim.
        assert record["id"] == GULAS.id
        assert record["name"] == GULAS.name
        assert record["author"] == GULAS.author
        assert record["ingredients"] == GULAS.ingredients
        assert record["instructions"] == GULAS.instructions
        # Domain classification.
        assert record["supported_goals"] == ["high-protein"]
        assert record["allergens"] == ["gluten", "milk", "eggs"]
        assert record["meal_type"] == "main-course"
        # Provenance metadata.
        meta = record["classification"]
        assert meta["source"] == "llm"
        assert meta["model"] == MODEL
        assert meta["classifier_version"] == "1"
        assert meta["prompt_version"] == "1"
        assert meta["review_status"] == "accepted"
        assert meta["fingerprint"] == compute_fingerprint(GULAS, MODEL)
        assert meta["classified_at"]
        # Evidence.
        evidence = record["classification_evidence"]
        assert evidence["goal_evidence"][0]["goal"] == "high-protein"

    async def test_czech_characters_remain_readable(
        self, paths: dict[str, Path]
    ) -> None:
        write_input(paths["input"], [GULAS])
        classifier = FakeClassifier(default=make_classification())
        await enrich_recipes(
            paths["input"], paths["output"], paths["report"], classifier, options()
        )
        raw = paths["output"].read_text(encoding="utf-8")
        assert "Hovězí guláš s karlovarským knedlíkem" in raw
        assert "\\u" not in raw

    async def test_low_confidence_becomes_needs_review(
        self, paths: dict[str, Path]
    ) -> None:
        write_input(paths["input"], [FISH_RECIPE])
        classifier = FakeClassifier(
            default=make_classification(
                goals=[DietaryGoal.PESCATARIAN],
                allergens=[Allergen.FISH],
                confidence=0.4,
            )
        )
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], classifier, options()
        )
        data = json.loads(paths["output"].read_text(encoding="utf-8"))
        assert data[0]["classification"]["review_status"] == "needs-review"
        assert data[0]["classification"]["validation_warnings"]
        assert report.needs_review == 1
        assert report.validation_warnings
        # Exactly one LLM call: no retry loop in the pipeline.
        assert classifier.calls == [FISH_RECIPE.id]

    async def test_resume_skips_completed_records(
        self, paths: dict[str, Path]
    ) -> None:
        write_input(paths["input"], [GULAS, FISH_RECIPE])
        first = FakeClassifier(default=make_classification())
        await enrich_recipes(
            paths["input"], paths["output"], paths["report"], first, options()
        )
        assert len(set(first.calls)) == 2

        second = FakeClassifier(default=make_classification())
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], second, options()
        )
        assert second.calls == []
        assert report.skipped_from_cache == 2
        assert report.classified == 0

    async def test_changed_recipe_is_reclassified_on_resume(
        self, paths: dict[str, Path]
    ) -> None:
        write_input(paths["input"], [GULAS])
        await enrich_recipes(
            paths["input"],
            paths["output"],
            paths["report"],
            FakeClassifier(default=make_classification()),
            options(),
        )
        changed = GULAS.model_copy(update={"ingredients": [*GULAS.ingredients, "Chilli"]})
        write_input(paths["input"], [changed])
        second = FakeClassifier(default=make_classification())
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], second, options()
        )
        assert set(second.calls) == {GULAS.id}
        assert report.classified == 1

    async def test_force_reclassifies_records(self, paths: dict[str, Path]) -> None:
        write_input(paths["input"], [GULAS])
        await enrich_recipes(
            paths["input"],
            paths["output"],
            paths["report"],
            FakeClassifier(default=make_classification()),
            options(),
        )
        second = FakeClassifier(default=make_classification())
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], second, options(force=True)
        )
        assert set(second.calls) == {GULAS.id}
        assert report.skipped_from_cache == 0

    async def test_one_failed_recipe_does_not_stop_batch(
        self, paths: dict[str, Path]
    ) -> None:
        write_input(paths["input"], [GULAS, FISH_RECIPE])
        classifier = FakeClassifier(
            responses={GULAS.id: ClassificationError("provider exploded")},
            default=make_classification(),
        )
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], classifier, options()
        )
        assert report.failed == 1
        assert report.classified == 1
        assert report.errors and "provider exploded" in report.errors[0]
        data = json.loads(paths["output"].read_text(encoding="utf-8"))
        assert [r["id"] for r in data] == [FISH_RECIPE.id]

    async def test_limit_caps_classified_recipes(self, paths: dict[str, Path]) -> None:
        recipes = [make_recipe(i, f"Recept {i}", ["Sůl", "Pepř"]) for i in range(1, 6)]
        write_input(paths["input"], recipes)
        classifier = FakeClassifier(default=make_classification())
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], classifier, options(limit=2)
        )
        assert report.classified == 2

    async def test_dry_run_makes_no_calls_and_writes_no_output(
        self, paths: dict[str, Path]
    ) -> None:
        write_input(paths["input"], [GULAS])
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], None, options(dry_run=True)
        )
        assert report.eligible == 1
        assert not paths["output"].exists()

    async def test_report_counts_are_correct(self, paths: dict[str, Path]) -> None:
        write_input(paths["input"], [GULAS, FISH_RECIPE, NUT_CAKE])
        classifier = FakeClassifier(
            responses={
                GULAS.id: make_classification(
                    goals=[DietaryGoal.HIGH_PROTEIN],
                    allergens=[Allergen.GLUTEN, Allergen.MILK, Allergen.EGGS],
                    confidence=0.94,
                ),
                FISH_RECIPE.id: make_classification(
                    goals=[
                        DietaryGoal.PESCATARIAN,
                        DietaryGoal.HIGH_PROTEIN,
                        DietaryGoal.DAIRY_FREE,
                        DietaryGoal.GLUTEN_FREE,
                        DietaryGoal.LOW_CARB,
                    ],
                    allergens=[Allergen.FISH],
                    confidence=0.9,
                ),
                NUT_CAKE.id: make_classification(
                    goals=[DietaryGoal.VEGETARIAN],
                    allergens=[
                        Allergen.GLUTEN,
                        Allergen.MILK,
                        Allergen.EGGS,
                        Allergen.NUTS,
                    ],
                    meal_type=MealType.DESSERT,
                    confidence=0.6,
                ),
            }
        )
        report = await enrich_recipes(
            paths["input"], paths["output"], paths["report"], classifier, options()
        )

        assert report.total_recipes == 3
        assert report.classified == 3
        assert report.accepted == 2
        assert report.needs_review == 1  # nut cake below confidence threshold
        assert report.goal_counts["high-protein"] == 2
        assert report.goal_counts["vegetarian"] == 1
        assert report.goal_counts["vegan"] == 0
        assert report.allergen_counts["gluten"] == 2
        assert report.allergen_counts["fish"] == 1
        assert report.meal_type_counts["main-course"] == 2
        assert report.meal_type_counts["dessert"] == 1
        assert report.average_confidence == pytest.approx(
            (0.94 + 0.9 + 0.6) / 3, abs=0.001
        )
        assert report.model == MODEL
        # Report file written and readable.
        saved = json.loads(paths["report"].read_text(encoding="utf-8"))
        assert saved["classified"] == 3


class TestPydanticAiClassifier:
    async def test_agent_returns_typed_output_via_test_model(self) -> None:
        classifier = PydanticAiRecipeClassifier(MODEL)
        test_model = TestModel(
            custom_output_args={
                "supported_goals": ["high-protein"],
                "allergens": ["gluten", "milk", "eggs"],
                "meal_type": "main-course",
                "confidence": 0.94,
                "goal_evidence": [
                    {"goal": "high-protein", "reason": "Beef is central."}
                ],
                "allergen_reason": "Dumplings, milk, eggs.",
                "meal_type_reason": "Stew with dumplings.",
            }
        )
        with classifier._agent.override(model=test_model):
            classification = await classifier.classify(GULAS)
        assert classification.supported_goals == [DietaryGoal.HIGH_PROTEIN]
        assert classification.meal_type is MealType.MAIN_COURSE
        assert classifier.total_usage.requests == 1
