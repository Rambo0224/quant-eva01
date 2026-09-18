from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


@dataclass
class MCPClient:
    name: str
    url: str
    auth_token: str
    timeout: float = 60.0

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Authorization": self.auth_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _post(self, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        resp = requests.post(self.url, headers=headers, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return {"json": resp.json(), "headers": resp.headers}

    def initialize(self) -> Dict[str, str]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "clientInfo": {"name": "macro-replay", "version": "0.1"},
                "capabilities": {"tools": {"listChanged": True, "call": True}},
            },
        }
        result = self._post(payload, self._headers())
        protocol = result["json"]["result"]["protocolVersion"]
        session_headers = self._headers({"MCP-Protocol-Version": protocol})
        session_id = result["headers"].get("MCP-Session-Id")
        if session_id:
            session_headers["MCP-Session-Id"] = session_id
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            self._post(notif, session_headers)
        except requests.HTTPError:
            pass
        return session_headers

    def call_tool(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        headers = self.initialize()
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        response = self._post(payload, headers)
        return response["json"]
