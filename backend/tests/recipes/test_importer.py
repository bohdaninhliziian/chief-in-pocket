"""Unit tests for recipe normalization and the CSV → JSON importer."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from recipes.importer import import_recipes, parse_row
from recipes.normalization import (
    clean_description,
    normalize_text,
    split_ingredients,
    split_instructions,
    strip_html,
)

HEADER = ["id", "name", "name", "author_note", "ingredients", "steps"]

VALID_ROW = [
    "14",
    "Kuřecí polévka s kokosovým mlékem",
    "Roman Vaněk",
    "Dejte nám vědět <strong>#rohlikchef</strong>",
    "Čerstvý koriandr, Česnek, Kokosové mléko",
    "Zázvor nakrájíme na tenké plátky., Vývar přivedeme k varu., Přidáme kokosové mléko.",
]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)


class TestTextNormalization:
    def test_html_tags_removed_and_entities_decoded(self) -> None:
        raw = "Dejte n&aacute;m v&#283;d&#283;t <strong>#rohlikchef</strong>&nbsp;dnes"
        assert clean_description(raw) == "Dejte nám vědět #rohlikchef dnes"

    def test_czech_diacritics_preserved(self) -> None:
        text = "Kuřecí polévka s kokosovým mlékem — žluťoučký kůň úpěl ďábelské ódy"
        assert normalize_text(text) == text
        assert strip_html(text) == text

    def test_whitespace_collapsed_and_nbsp_normalized(self) -> None:
        assert normalize_text("  Hovězí\u00a0 guláš \t s\r\n knedlíkem  ") == (
            "Hovězí guláš s knedlíkem"
        )


class TestIngredientSplitting:
    def test_splits_and_trims(self) -> None:
        raw = "Bobkový list, Drcený kmín ,  Hovězí plec vcelku, Máslo, Mléko"
        assert split_ingredients(raw) == [
            "Bobkový list",
            "Drcený kmín",
            "Hovězí plec vcelku",
            "Máslo",
            "Mléko",
        ]

    def test_duplicates_removed_case_insensitively_keeping_order(self) -> None:
        raw = "Máslo, Sůl, máslo, MÁSLO, Pepř"
        assert split_ingredients(raw) == ["Máslo", "Sůl", "Pepř"]

    def test_empty_values_removed(self) -> None:
        raw = ", Máslo, ,  , Sůl,"
        assert split_ingredients(raw) == ["Máslo", "Sůl"]


class TestInstructionSplitting:
    def test_splits_on_period_comma_boundary_only(self) -> None:
        raw = (
            "Cibuli nakrájíme nahrubo. Maso nakrájíme na kostky o hraně 4 cm., "
            "Jakmile je maso měkké, osolíme a opepříme."
        )
        assert split_instructions(raw) == [
            "Cibuli nakrájíme nahrubo. Maso nakrájíme na kostky o hraně 4 cm.",
            "Jakmile je maso měkké, osolíme a opepříme.",
        ]

    def test_blank_lines_separate_steps(self) -> None:
        raw = "První krok receptu zde.\r\n\r\nDruhý krok receptu tady."
        assert split_instructions(raw) == [
            "První krok receptu zde.",
            "Druhý krok receptu tady.",
        ]

    def test_tiny_fragments_merged_into_previous_step(self) -> None:
        raw = "Steak servírujeme s omáčkou a hranolky., A hotovo."
        steps = split_instructions(raw)
        assert steps == ["Steak servírujeme s omáčkou a hranolky. A hotovo."]

    def test_unsplittable_text_kept_as_single_step(self) -> None:
        raw = "Vše smícháme dohromady a podáváme"
        assert split_instructions(raw) == [raw]


class TestRowParsing:
    def test_invalid_id_rejected(self) -> None:
        row = VALID_ROW.copy()
        row[0] = "abc"
        with pytest.raises(ValueError):
            parse_row(row)

    def test_negative_id_rejected(self) -> None:
        row = VALID_ROW.copy()
        row[0] = "-5"
        with pytest.raises(ValidationError):
            parse_row(row)

    def test_missing_name_rejected(self) -> None:
        row = VALID_ROW.copy()
        row[1] = "   "
        with pytest.raises(ValidationError):
            parse_row(row)

    def test_empty_ingredients_rejected(self) -> None:
        row = VALID_ROW.copy()
        row[4] = " , , "
        with pytest.raises(ValidationError):
            parse_row(row)


class TestImport:
    def test_multiline_quoted_fields_survive(self, tmp_path: Path) -> None:
        row = VALID_ROW.copy()
        row[5] = "První krok receptu zde.\n\nDruhý krok receptu tady."
        input_path = tmp_path / "recipes.csv"
        output_path = tmp_path / "recipes.json"
        write_csv(input_path, [row])

        summary = import_recipes(input_path, output_path)

        assert summary.imported == 1
        recipe = json.loads(output_path.read_text(encoding="utf-8"))[0]
        assert recipe["instructions"] == [
            "První krok receptu zde.",
            "Druhý krok receptu tady.",
        ]

    def test_import_continues_after_invalid_row(self, tmp_path: Path) -> None:
        bad_row = VALID_ROW.copy()
        bad_row[0] = "not-a-number"
        good_row = VALID_ROW.copy()
        good_row[0] = "15"
        input_path = tmp_path / "recipes.csv"
        output_path = tmp_path / "recipes.json"
        write_csv(input_path, [VALID_ROW, bad_row, good_row])

        summary = import_recipes(input_path, output_path)

        assert summary.rows_read == 3
        assert summary.imported == 2
        assert summary.skipped == 1
        assert len(summary.warnings) == 1
        assert "row 3" in summary.warnings[0]

    def test_id_range_and_limit(self, tmp_path: Path) -> None:
        rows = []
        for recipe_id in range(10, 20):
            row = VALID_ROW.copy()
            row[0] = str(recipe_id)
            rows.append(row)
        input_path = tmp_path / "recipes.csv"
        output_path = tmp_path / "recipes.json"
        write_csv(input_path, rows)

        summary = import_recipes(
            input_path, output_path, start_id=12, end_id=18, limit=3
        )

        assert summary.imported == 3
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert [r["id"] for r in data] == [12, 13, 14]

    def test_generated_json_matches_schema(self, tmp_path: Path) -> None:
        input_path = tmp_path / "recipes.csv"
        output_path = tmp_path / "recipes.json"
        write_csv(input_path, [VALID_ROW])

        import_recipes(input_path, output_path)

        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data == [
            {
                "id": 14,
                "name": "Kuřecí polévka s kokosovým mlékem",
                "author": "Roman Vaněk",
                "description": "Dejte nám vědět #rohlikchef",
                "ingredients": [
                    "Čerstvý koriandr",
                    "Česnek",
                    "Kokosové mléko",
                ],
                "instructions": [
                    "Zázvor nakrájíme na tenké plátky.",
                    "Vývar přivedeme k varu.",
                    "Přidáme kokosové mléko.",
                ],
                "dietary_tags": [],
                "allergens": [],
                "meal_type": None,
            }
        ]

    def test_output_directory_created(self, tmp_path: Path) -> None:
        input_path = tmp_path / "recipes.csv"
        output_path = tmp_path / "nested" / "dir" / "recipes.json"
        write_csv(input_path, [VALID_ROW])

        summary = import_recipes(input_path, output_path)

        assert output_path.is_file()
        assert summary.output_path == output_path
