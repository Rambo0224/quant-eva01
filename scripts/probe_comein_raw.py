from __future__ import annotations

import os
import requests


def main() -> None:
    url = os.environ.get("COMEIN_MCP_URL", "https://mcp-server-global.comein.cn/mcp-servers/mcp-server-brm/sse")
    key = os.environ["COMEIN_MCP_KEY"]
    headers = {"Accept": "text/event-stream", "x-mcp-key": key}
    session = requests.Session()
    session.trust_env = False
    stream = session.get(url, headers=headers, stream=True, timeout=30)
    print("GET", stream.status_code, stream.headers.get("content-type"))
    endpoint = None
    lines = []
    for raw in stream.iter_lines(decode_unicode=True):
        line = raw or ""
        lines.append(line)
        if line.startswith("data:"):
            endpoint = line.split(":", 1)[1].strip()
            break
    print("SSE", lines)
    post_url = "https://mcp-server-global.comein.cn" + endpoint
    response = session.post(
        post_url,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "x-mcp-key": key},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "omnisignal", "version": "0.1"}, "capabilities": {}}},
        timeout=30,
    )
    print("POST", response.status_code, response.headers.get("content-type"), response.text[:1000])
    stream.close()
    session.close()


if __name__ == "__main__":
    main()
