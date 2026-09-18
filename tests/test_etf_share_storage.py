import pandas as pd

from macro_replay.db import insert_missing_etf_share_rows


def test_insert_missing_etf_share_rows_is_keyed_by_symbol_date(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from macro_replay.db import ensure_db

    conn = ensure_db()
    try:
        frame = pd.DataFrame(
            [
                {
                    "symbol": "510050",
                    "date": "2026-07-20",
                    "name": "上证50ETF",
                    "exchange": "SSE",
                    "category": "broad",
                    "shares": 8322067000.0,
                }
            ]
        )
        assert insert_missing_etf_share_rows(conn, "akshare_etf_scale_sse", frame) == 1
        assert insert_missing_etf_share_rows(conn, "akshare_etf_scale_sse", frame) == 0
        stored = conn.execute("SELECT symbol, shares FROM etf_share_daily").fetchall()
        assert stored == [("510050", 8322067000.0)]
    finally:
        conn.close()
