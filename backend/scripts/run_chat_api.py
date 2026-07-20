#!/usr/bin/env python3
"""Start the Chef in My Pocket conversational chat API.

Usage:
    uv run python scripts/run_chat_api.py [--host 127.0.0.1] [--port 8000]
        [--data-path PATH]

Requires OPENAI_API_KEY (env or backend/.env). Optional env:
CHAT_AGENT_MODEL (default openai:gpt-5-mini), CHAT_MODEL_TIMEOUT_SECONDS,
CHAT_HISTORY_MAX_MESSAGES, RECIPES_ENRICHED_PATH.

Sessions are in-memory: every conversation is lost when the process stops.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

import uvicorn
from dotenv import load_dotenv

logger = logging.getLogger("run_chat_api")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--data-path",
        type=Path,
        default=None,
        help="Enriched recipes JSON (default: data/processed/recipes_enriched.json)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="DEBUG adds per-message history detail (kinds of every new message)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s %(message)s",
    )
    if args.log_level == "DEBUG":
        # Third-party DEBUG (httpx, openai, uvicorn) drowns out the history
        # trail; keep the noise at INFO and our own modules at DEBUG.
        for noisy in ("httpx", "httpcore", "openai", "uvicorn", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.INFO)
    load_dotenv(BACKEND_DIR / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY is not set (env or backend/.env); aborting")
        raise SystemExit(1)

    from recipes.chat.api import create_app

    app = create_app(data_path=args.data_path)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
