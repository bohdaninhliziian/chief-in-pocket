"""Reusable text normalization helpers for recipe imports.

All functions are pure and operate on plain strings so they can be reused
unchanged when the importer is replaced by a database-backed pipeline.
"""

from __future__ import annotations

import html
import re
import unicodedata

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
# Non-breaking spaces (U+00A0) and narrow no-break spaces (U+202F) show up in
# text exported from CMS systems; treat them as plain spaces.
_NBSP_RE = re.compile("[\u00a0\u202f]")

# A step boundary in the source data is a comma that directly follows a
# sentence-ending period, e.g. "... na kostky., Jakmile je maso měkké ...".
# Plain commas inside a step never follow a period, so this does not split
# mid-sentence enumerations or decimal values.
_STEP_BOUNDARY_RE = re.compile(r"(?<=\.)\s*,\s+")
_BLANK_LINE_RE = re.compile(r"(?:\r?\n)+")

# Fragments shorter than this (after normalization) are merged into the
# preceding step instead of being emitted as standalone "steps".
MIN_STEP_LENGTH = 15


def normalize_ingredient_key(text: str) -> str:
    """Canonical comparison key for an ingredient name.

    Unicode-safe (NFC), whitespace-normalized and case-insensitive, so
    " mléko " and "Mléko" compare equal. The original ingredient text is
    never modified — this key is used only for matching.
    """
    return normalize_text(unicodedata.normalize("NFC", text)).casefold()


def strip_html(text: str) -> str:
    """Remove HTML tags and decode HTML entities, keeping the text content."""
    without_tags = _HTML_TAG_RE.sub(" ", text)
    return html.unescape(without_tags)


def normalize_text(text: str) -> str:
    """Collapse whitespace runs, normalize non-breaking spaces, and strip ends.

    UTF-8 content (including Czech diacritics) passes through unmodified.
    """
    text = _NBSP_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def clean_description(text: str) -> str:
    """Full cleanup for description fields: strip HTML, then normalize."""
    return normalize_text(strip_html(text))


def split_ingredients(raw: str, separator: str = ",") -> list[str]:
    """Split an ingredient string into a normalized, deduplicated list.

    Splitting is isolated here because the separator strategy may change with
    future datasets. Duplicates are removed case-insensitively while the first
    occurrence keeps its position and capitalization.
    """
    seen: set[str] = set()
    ingredients: list[str] = []
    for part in raw.split(separator):
        ingredient = normalize_text(strip_html(part))
        if not ingredient:
            continue
        key = ingredient.casefold()
        if key in seen:
            continue
        seen.add(key)
        ingredients.append(ingredient)
    return ingredients


def split_instructions(
    raw: str,
    boundary: re.Pattern[str] = _STEP_BOUNDARY_RE,
    min_step_length: int = MIN_STEP_LENGTH,
) -> list[str]:
    """Split instruction text into ordered steps.

    The dataset separates steps with a comma that follows a sentence-ending
    period ("step one., Step two"), and occasionally with blank lines. We
    split on those explicit boundaries only — never on bare periods, because
    abbreviations and decimals ("4 cm.", "0,5 l") occur inside steps.

    Fragments shorter than ``min_step_length`` are merged into the previous
    step so no text is lost. If the text has no recognizable boundaries, it is
    returned unchanged as a single step.
    """
    steps: list[str] = []
    for chunk in _BLANK_LINE_RE.split(raw):
        for part in boundary.split(chunk):
            step = normalize_text(strip_html(part))
            if not step:
                continue
            if len(step) < min_step_length and steps:
                steps[-1] = f"{steps[-1]} {step}"
            else:
                steps.append(step)
    return steps
