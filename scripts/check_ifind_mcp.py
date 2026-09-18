from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import requests


def load_config() -> dict:
    configured = os.environ.get("OMNISIGNAL_MCP_CONFIG", "").strip()
    path = Path(configured) if configured else Path("config/mcp_servers.toml")
    if not path.exists():
        raise FileNotFoundError(
            "No MCP config found. Set OMNISIGNAL_MCP_CONFIG to a read-only iFinD TOML file."
        )
    return tomllib.loads(path.read_text(encoding="utf-8"))


def list_tools(server: dict) -> list[dict]:
    headers = {
        "Authorization": server["auth_token"],
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init = requests.post(
        server["url"],
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "clientInfo": {"name": "omnisignal-check", "version": "0.1"},
                "capabilities": {"tools": {"listChanged": True, "call": True}},
            },
        },
        timeout=60,
    )
    init.raise_for_status()
    payload = init.json()
    headers["MCP-Protocol-Version"] = payload["result"]["protocolVersion"]
    if init.headers.get("MCP-Session-Id"):
        headers["MCP-Session-Id"] = init.headers["MCP-Session-Id"]
    requests.post(
        server["url"],
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=60,
    )
    response = requests.post(
        server["url"],
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        timeout=60,
    )
    response.raise_for_status()
    return response.json().get("result", {}).get("tools", [])


def main() -> None:
    config = load_config()
    for name in ("hexin-ifind-ds-edb-mcp", "hexin-ifind-ds-stock-mcp"):
        if name not in config:
            continue
        tools = list_tools(config[name])
        print(json.dumps({"server": name, "tools": [tool.get("name") for tool in tools]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
