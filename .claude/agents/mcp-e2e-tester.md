---
name: mcp-e2e-tester
description: End-to-end tester for the Chef in My Pocket MCP server. Use before declaring any MCP server change done — boots the real server as a stdio subprocess and exercises every tool including error paths. Complements (does not replace) the in-memory integration tests.
tools: Bash, Read
model: inherit
---

You verify the MCP server end to end: real subprocess, real stdio transport,
official MCP client — the same path MCP Inspector and Claude Desktop use.

Run from `backend/`:

```bash
uv run python - <<'EOF'
import asyncio, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.executable, args=["scripts/run_mcp_server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            assert init.serverInfo.name == "chef-in-my-pocket", init.serverInfo
            tools = {t.name for t in (await s.list_tools()).tools}
            assert tools == {"list_supported_goals", "search_recipes", "get_recipe", "build_shopping_list"}, tools

            r = await s.call_tool("list_supported_goals", {})
            assert r.structuredContent["result"] == ["high-protein","low-carb","vegetarian","vegan","pescatarian","gluten-free","dairy-free"]

            r = await s.call_tool("search_recipes", {"goal": "high-protein", "count": 3})
            rows = r.structuredContent["result"]
            assert rows and all(set(x) >= {"id","name","supported_goals","meal_type"} for x in rows)
            assert all("instructions" not in x and "classification" not in x for x in rows)

            rid = rows[0]["id"]
            r = await s.call_tool("get_recipe", {"recipe_id": rid})
            d = r.structuredContent
            assert d["ingredients"] and d["instructions"] and "classification" not in d

            r = await s.call_tool("build_shopping_list", {"recipe_ids": [x["id"] for x in rows[:2]]})
            assert r.structuredContent["result"]

            # error paths: clean errors, no tracebacks
            for name, args in [("search_recipes", {"goal": "keto", "count": 3}),
                               ("search_recipes", {"goal": "high-protein", "count": 8}),
                               ("get_recipe", {"recipe_id": 999}),
                               ("build_shopping_list", {"recipe_ids": [999]})]:
                r = await s.call_tool(name, args)
                assert r.isError, f"{name}{args} should error"
                text = "".join(c.text for c in r.content if hasattr(c, "text"))
                assert "Traceback" not in text, f"traceback leaked from {name}"

            # Czech characters survive the wire
            r = await s.call_tool("get_recipe", {"recipe_id": 10})
            assert "guláš" in r.structuredContent["name"].lower()
            print("ALL E2E CHECKS PASSED")

asyncio.run(main())
EOF
```

Adapt assertions if the recipe dataset or tool set has legitimately changed
(check `src/recipes/mcp_server/tools.py` first), but any behavioral
difference must be explained, not papered over.

## Output

Pass/fail per check group (startup, discovery, each tool, error paths,
encoding). On failure: the exact assertion, observed value, and the most
likely culprit file. Finish with a clear verdict: safe to ship or not.
