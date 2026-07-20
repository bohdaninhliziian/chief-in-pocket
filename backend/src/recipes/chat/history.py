"""Message-history trimming for chat sessions.

History provides conversational continuity only — the canonical meal plan
lives in structured session state, so trimming old messages never loses
the current plan.
"""

from __future__ import annotations

import logging
import os

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

logger = logging.getLogger(__name__)

HISTORY_LIMIT_ENV_VAR = "CHAT_HISTORY_MAX_MESSAGES"
DEFAULT_HISTORY_MAX_MESSAGES = 40


def history_limit() -> int:
    """Resolve the history cap from the environment, falling back to the
    default on a malformed value — a bad env var must never fail a request."""
    raw = os.environ.get(HISTORY_LIMIT_ENV_VAR)
    if raw is None:
        return DEFAULT_HISTORY_MAX_MESSAGES
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "invalid %s=%r; using default %d",
            HISTORY_LIMIT_ENV_VAR,
            raw,
            DEFAULT_HISTORY_MAX_MESSAGES,
        )
        return DEFAULT_HISTORY_MAX_MESSAGES


def _starts_user_turn(message: ModelMessage) -> bool:
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )


def trim_history(
    messages: list[ModelMessage], max_messages: int
) -> list[ModelMessage]:
    """Keep at most ~``max_messages``, dropping the oldest whole turns.

    The cut happens only at a message that starts a user turn, so
    tool-call/tool-return pairs required for model context are never
    split. If no boundary exists at or after the target cut point the
    last boundary before it is used; with no boundaries at all the
    history is returned unchanged.
    """
    if max_messages <= 0 or len(messages) <= max_messages:
        logger.debug(
            "history trim: %d message(s) within cap %d — nothing dropped",
            len(messages),
            max_messages,
        )
        return list(messages)
    target = len(messages) - max_messages
    boundaries = [i for i, message in enumerate(messages) if _starts_user_turn(message)]
    if not boundaries:
        logger.warning(
            "history trim: %d message(s) over cap %d but no user-turn boundary "
            "found — keeping everything to avoid splitting tool-call pairs",
            len(messages),
            max_messages,
        )
        return list(messages)
    cut = next((i for i in boundaries if i >= target), boundaries[-1])
    logger.info(
        "history trim: %d message(s) over cap %d — dropping the oldest %d, "
        "cut at user-turn index %d",
        len(messages),
        max_messages,
        cut,
        cut,
    )
    return list(messages[cut:])
