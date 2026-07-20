#!/usr/bin/env python3
"""Interactive CLI client for the chat API (manual demonstration).

Start the API first:
    uv run python scripts/run_chat_api.py

Then chat:
    uv run python scripts/chat_demo.py [--url http://127.0.0.1:8000]

The client keeps the session id returned by the first response, prints the
assistant's reply and renders the structured meal plan after every message
so the canonical state can be inspected turn by turn. Exit with 'exit',
Ctrl-C or Ctrl-D.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

# This script is a plain HTTP client for manual use; user-facing print is
# its job (same exception as the other CLI summaries).


def render_plan(plan: dict[str, Any] | None) -> None:
    if not plan:
        return
    print("\n  Current plan"
          f" (goal: {plan['dietary_goal']},"
          f" days: {plan['planned_days']}/{plan['requested_days']})")
    for meal in plan["meals"]:
        print(f"    {meal['day_label']:<10} #{meal['recipe_id']:<4} {meal['recipe_name']}")
    if plan["excluded_ingredients"]:
        print(f"  Excluded: {', '.join(plan['excluded_ingredients'])}")
    print(f"  Shopping list ({len(plan['shopping_list'])} items): "
          + ", ".join(item["ingredient"] for item in plan["shopping_list"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    session_id: str | None = None
    print("Chef in My Pocket — chat demo. Type 'exit' to quit.")
    with httpx.Client(base_url=args.url, timeout=120.0) as client:
        while True:
            try:
                message = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not message or message.lower() in {"exit", "quit"}:
                break
            try:
                response = client.post(
                    "/chat", json={"session_id": session_id, "message": message}
                )
            except httpx.HTTPError as exc:
                print(f"[connection error: {exc}]")
                continue
            if response.status_code != 200:
                detail = response.json().get("detail", response.text)
                print(f"[error {response.status_code}] {detail}")
                continue
            payload = response.json()
            session_id = payload["session_id"]
            print(f"\nchef> {payload['message']}")
            render_plan(payload["meal_plan"])
    print("\nbye")
    return None


if __name__ == "__main__":
    sys.exit(main())
