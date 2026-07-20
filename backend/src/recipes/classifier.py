"""Recipe classifier abstraction and the Pydantic AI implementation.

Architecture note: ``RecipeClassifier`` is a small protocol so the pipeline
can swap implementations later (a different provider, a locale-specific
validating classifier, a cached/manual source) without touching the
enrichment pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Protocol

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.usage import RunUsage

from recipes.classification import RecipeClassification
from recipes.models import Recipe
from recipes.recipe_classifier_prompt import (
    CLASSIFIER_SYSTEM_PROMPT,
    build_user_prompt,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "openai:gpt-5-mini"
MODEL_ENV_VAR = "RECIPE_CLASSIFIER_MODEL"
API_KEY_ENV_VAR = "OPENAI_API_KEY"

# HTTP statuses worth retrying with backoff; everything else is permanent.
_TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class ClassificationError(Exception):
    """A recipe could not be classified (permanent, after bounded retries)."""


class RecipeClassifier(Protocol):
    async def classify(self, recipe: Recipe) -> RecipeClassification:
        """Classify one recipe."""
        ...


def resolve_model_name(cli_value: str | None = None) -> str:
    """Model resolution order: CLI flag > RECIPE_CLASSIFIER_MODEL > default."""
    return cli_value or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL


def require_api_key() -> None:
    """Fail fast with a clear message when no API key is configured."""
    if not os.environ.get(API_KEY_ENV_VAR):
        raise ClassificationError(
            f"{API_KEY_ENV_VAR} is not set. Export it before running the "
            "enrichment pipeline (the key itself is never logged)."
        )


class PydanticAiRecipeClassifier:
    """LLM classifier built on a Pydantic AI agent with typed output.

    Structured-output validation errors are retried by Pydantic AI itself
    (``retries=2``); transient provider failures are retried here with
    bounded exponential backoff.
    """

    def __init__(
        self,
        model_name: str,
        *,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
    ) -> None:
        self.model_name = model_name
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.total_usage = RunUsage()
        self._agent: Agent[None, RecipeClassification] = Agent(
            model=model_name,
            output_type=RecipeClassification,
            system_prompt=CLASSIFIER_SYSTEM_PROMPT,
            retries=2,
            # Resolve the model lazily so the classifier can be constructed
            # (and overridden with a test model) without provider credentials.
            defer_model_check=True,
        )

    async def classify(self, recipe: Recipe) -> RecipeClassification:
        prompt = build_user_prompt(recipe)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await self._agent.run(prompt)
            except ModelHTTPError as exc:
                if exc.status_code in {401, 403}:
                    raise ClassificationError(
                        f"authentication with the model provider failed "
                        f"(HTTP {exc.status_code}); check {API_KEY_ENV_VAR}"
                    ) from exc
                # 429 with insufficient_quota is a billing problem, not a
                # rate limit — retrying can never succeed.
                if "insufficient_quota" in str(exc.body):
                    raise ClassificationError(
                        "the provider account has no remaining quota "
                        "(insufficient_quota); add credits or use another key"
                    ) from exc
                if exc.status_code not in _TRANSIENT_STATUS_CODES:
                    raise ClassificationError(
                        f"provider error HTTP {exc.status_code} for recipe {recipe.id}"
                    ) from exc
                last_error = exc
            except ModelAPIError as exc:
                last_error = exc  # connection-level issue: retry
            except UnexpectedModelBehavior as exc:
                raise ClassificationError(
                    f"model returned invalid structured output for recipe "
                    f"{recipe.id} after internal retries: {exc}"
                ) from exc
            else:
                self.total_usage.incr(result.usage)
                return result.output

            if attempt < self.max_retries:
                delay = self.backoff_base_seconds * (2**attempt)
                logger.warning(
                    "transient provider failure for recipe %s "
                    "(attempt %d/%d), retrying in %.1fs: %s",
                    recipe.id,
                    attempt + 1,
                    self.max_retries + 1,
                    delay,
                    last_error,
                )
                await asyncio.sleep(delay)

        raise ClassificationError(
            f"transient provider failures exhausted retries for recipe "
            f"{recipe.id}: {last_error}"
        ) from last_error
