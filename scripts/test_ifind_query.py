#!/usr/bin/env python3
import sys

from macro_replay.config import Indicator, load_mcp_servers
from macro_replay.db import ensure_db
from macro_replay.ingest import fetch_indicator


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: test_ifind_query.py '<query>' <start> <end>")
        raise SystemExit(1)
    query = sys.argv[1]
    start = sys.argv[2]
    end = sys.argv[3]

    servers = load_mcp_servers()
    server = servers["hexin-ifind-ds-edb-mcp"]
    indicator = Indicator(
        id="ifind-test",
        title="ifind-test",
        description="",
        server="hexin-ifind-ds-edb-mcp",
        tool="get_edb_data",
        arguments={"query": query},
        chart={},
        theme="test",
        source="ifind",
    )
    conn = ensure_db()
    resp = fetch_indicator(conn, indicator, server, start, end)
    print(resp)


if __name__ == "__main__":
    main()
