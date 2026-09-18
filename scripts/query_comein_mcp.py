from __future__ import annotations

import argparse
import base64
import json
import os

from datahub.adapters.comein_mcp import ComeinMcpClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Call one read-only Comein Finance MCP tool")
    parser.add_argument("tool")
    parser.add_argument("arguments", nargs="?", help="JSON object passed as tool arguments")
    parser.add_argument("--arguments-base64", help="Base64-encoded UTF-8 JSON arguments")
    args = parser.parse_args()
    if not os.environ.get("COMEIN_MCP_KEY"):
        raise SystemExit("COMEIN_MCP_KEY is required; the key is intentionally not stored in the repository")
    raw_arguments = args.arguments
    if args.arguments_base64:
        raw_arguments = base64.b64decode(args.arguments_base64).decode("utf-8")
    if not raw_arguments:
        raise SystemExit("arguments or --arguments-base64 is required")
    with ComeinMcpClient() as client:
        result = client.call_tool(args.tool, json.loads(raw_arguments))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
