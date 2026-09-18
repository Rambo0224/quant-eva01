from __future__ import annotations

from datetime import date
from typing import Any, Dict

from .config import Indicator, MCPServer
from .db import store_payload, upsert_observations
from .mcp_client import MCPClient
from .sources.ifind import format_arguments, parse_line_series


def fetch_indicator(conn, indicator: Indicator, server: MCPServer, start: str, end: str) -> Dict[str, Any]:
    client = MCPClient(name=server.name, url=server.url, auth_token=server.auth_token)
    arguments = format_arguments(indicator.arguments, start, end)
    response = client.call_tool(indicator.tool, arguments)
    request_meta = {"tool": indicator.tool, "arguments": arguments}
    store_payload(conn, indicator.id, server.name, request_meta, response)
    rows, unit = parse_line_series(response)
    if rows:
        series_id = f"{indicator.id}:mcp"
        upsert_observations(conn, indicator.id, series_id, server.name, rows, unit=unit)
    return response
