"""MCP server exposing the runtime recipe services as tools.

This layer is intentionally thin: no business logic lives here, only
input/output mapping around RecipeService and ShoppingListService.
"""

from recipes.mcp_server.server import create_server

__all__ = ["create_server"]
