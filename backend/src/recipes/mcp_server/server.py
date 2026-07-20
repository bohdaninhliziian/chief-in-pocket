"""Chef in My Pocket MCP server.

Business logic is intentionally kept outside MCP — this layer only exposes
the deterministic runtime services (recipe search, recipe detail, shopping
lists) as tools. No LLM is called here; recipe metadata was generated
offline by the enrichment pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from recipes.mcp_server.dependencies import Dependencies, build_dependencies
from recipes.mcp_server.meal_plan_tools import register_meal_plan_tools
from recipes.mcp_server.tools import register_tools

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = """\
Chef in My Pocket: deterministic recipe search over an offline-classified
Czech recipe collection. Typical flow: list_supported_goals (discover
goals) -> search_recipes (pick candidates) -> get_recipe (full details)
-> build_shopping_list (merged ingredients for chosen recipe ids).
Meal-plan sessions: create_meal_plan / replace_meal / get_meal_plan /
add_ingredient_exclusion run complete deterministic workflows and keep
the per-session plan server-side (0-based day_index; days are numbered
"Day 1", "Day 2", …, never calendar weekdays).
Recipes have no quantities or nutrition values; high-protein and low-carb
are ingredient-based approximations.
"""


def create_server(
    data_path: Path | None = None,
    *,
    dependencies: Dependencies | None = None,
) -> FastMCP:
    """Build the MCP server with dependencies wired exactly once.

    The chat API passes pre-built ``dependencies`` so both layers share
    one repository and one session store.
    """
    mcp = FastMCP(name="chef-in-my-pocket", instructions=SERVER_INSTRUCTIONS)
    deps = dependencies or build_dependencies(data_path)
    register_tools(mcp, deps)
    register_meal_plan_tools(mcp, deps)
    return mcp


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = create_server()
    logger.info("starting Chef in My Pocket MCP server (stdio)")
    server.run()  # stdio transport


if __name__ == "__main__":
    main()
