from __future__ import annotations

import json
import os
import sys

from datahub.adapters.comein_mcp import ComeinMcpClient


def main() -> None:
    if not os.environ.get("COMEIN_MCP_KEY"):
        raise SystemExit("COMEIN_MCP_KEY is required; the key is intentionally not stored in the repository")
    with ComeinMcpClient() as client:
        tools = client.list_tools()
        if "--names" in sys.argv:
            print(json.dumps([tool.name for tool in tools], ensure_ascii=False, indent=2))
            return
        selected = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
        if selected:
            chosen = [tool for tool in tools if tool.name in selected]
            print(json.dumps([{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema} for tool in chosen], ensure_ascii=False, indent=2))
            return
        print(json.dumps([{"name": tool.name, "description": tool.description, "input_schema": tool.input_schema} for tool in tools], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
