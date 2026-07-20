#!/usr/bin/env python3
"""Start the Chef in My Pocket MCP server on stdio.

Usage:
    uv run python scripts/run_mcp_server.py

With MCP Inspector:
    npx @modelcontextprotocol/inspector uv run python scripts/run_mcp_server.py

Set RECIPES_ENRICHED_PATH to point at a different enriched dataset.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "src"))

from recipes.mcp_server.server import main

if __name__ == "__main__":
    main()
