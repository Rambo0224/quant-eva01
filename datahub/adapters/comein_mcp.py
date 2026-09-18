from __future__ import annotations

from dataclasses import dataclass
import json
import os
from queue import Queue
import threading
from typing import Any
from urllib.parse import urljoin
from pathlib import Path

import requests


class ComeinMcpUnavailable(RuntimeError):
    """Raised when the Comein Finance MCP transport cannot be used."""


@dataclass(frozen=True)
class ComeinTool:
    name: str
    description: str
    input_schema: dict[str, Any]


def _local_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "comein_mcp.local.env"


def _load_local_config() -> dict[str, str]:
    path = _local_config_path()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def save_local_config(url: str, key: str) -> None:
    path = _local_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"COMEIN_MCP_URL={url.strip()}\nCOMEIN_MCP_KEY={key.strip()}\n",
        encoding="utf-8",
    )


class ComeinMcpClient:
    """Small SSE MCP client; credentials are read only from COMEIN_MCP_KEY."""

    def __init__(self, url: str | None = None, key: str | None = None, timeout: float = 60.0) -> None:
        local_config = _load_local_config()
        local_url = local_config.get("COMEIN_MCP_URL")
        self.url = url or local_url or os.environ.get(
            "COMEIN_MCP_URL",
            "https://mcp-server-global.comein.cn/mcp-servers/mcp-server-brm/sse",
        )
        if key is not None:
            self.key = key
        elif "COMEIN_MCP_KEY" in local_config:
            self.key = local_config["COMEIN_MCP_KEY"]
        else:
            self.key = os.environ.get("COMEIN_MCP_KEY", "")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"Accept": "application/json, text/event-stream"})
        if self.key:
            self.session.headers["x-mcp-key"] = self.key
        self._stream = None
        self._events: Queue[dict[str, Any]] = Queue()
        self._reader: threading.Thread | None = None

    def __enter__(self) -> "ComeinMcpClient":
        self._open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _open(self) -> None:
        try:
            self._stream = self.session.get(
                self.url,
                headers={"Accept": "text/event-stream"},
                stream=True,
                timeout=self.timeout,
            )
            self._stream.raise_for_status()
            endpoint = None
            for raw in self._stream.iter_lines(decode_unicode=True):
                line = raw or ""
                if line.startswith("data:"):
                    endpoint = line.split(":", 1)[1].strip()
                    break
            if not endpoint:
                raise ComeinMcpUnavailable("Comein SSE did not return a POST endpoint")
            self._post_url = urljoin(self.url, endpoint)
            self._reader = threading.Thread(target=self._read_events, daemon=True)
            self._reader.start()
            initialized = self._post(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "clientInfo": {"name": "omnisignal", "version": "0.1"},
                    "capabilities": {},
                },
            )
            self.protocol_version = initialized.get("result", {}).get("protocolVersion", "2025-11-25")
            self._notify("notifications/initialized", {})
        except Exception as exc:
            self.close()
            if isinstance(exc, ComeinMcpUnavailable):
                raise
            raise ComeinMcpUnavailable(str(exc)) from exc

    def _read_events(self) -> None:
        event_name = "message"
        data_lines: list[str] = []
        try:
            for raw in self._stream.iter_lines(decode_unicode=True):
                line = raw or ""
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].lstrip())
                elif not line and data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                        if isinstance(payload, dict):
                            payload["_event"] = event_name
                            self._events.put(payload)
                    except json.JSONDecodeError:
                        pass
                    event_name = "message"
                    data_lines = []
        except Exception as exc:
            self._events.put({"error": str(exc)})

    def _post(self, request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            self._post_url,
            headers={"Content-Type": "application/json", "MCP-Protocol-Version": getattr(self, "protocol_version", "2025-11-25")},
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            timeout=self.timeout,
        )
        response.raise_for_status()
        if "application/json" in response.headers.get("content-type", ""):
            payload = response.json()
            if "error" in payload:
                raise ComeinMcpUnavailable(str(payload["error"]))
            return payload
        while True:
            event = self._events.get(timeout=self.timeout)
            if "error" in event:
                raise ComeinMcpUnavailable(event["error"])
            if event.get("id") == request_id:
                if "error" in event:
                    raise ComeinMcpUnavailable(str(event["error"]))
                return event

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        response = self.session.post(
            self._post_url,
            headers={"Content-Type": "application/json", "MCP-Protocol-Version": getattr(self, "protocol_version", "2025-11-25")},
            json={"jsonrpc": "2.0", "method": method, "params": params},
            timeout=self.timeout,
        )
        response.raise_for_status()

    def list_tools(self) -> list[ComeinTool]:
        payload = self._post(2, "tools/list", {})
        tools = payload.get("result", {}).get("tools", [])
        return [ComeinTool(item.get("name", ""), item.get("description", ""), item.get("inputSchema", {})) for item in tools]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = self._post(3, "tools/call", {"name": name, "arguments": arguments})
        return payload.get("result", {})

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
        self.session.close()
