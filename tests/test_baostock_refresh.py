import duckdb
import pandas as pd

from scripts.fetch_baostock_equity_bars import _bounds


def test_baostock_bounds_ignore_other_providers() -> None:
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE equity_daily_bars (source_id TEXT, symbol TEXT, obs_time TIMESTAMP)")
    conn.executemany(
        "INSERT INTO equity_daily_bars VALUES (?, ?, ?)",
        [
            ("openbb_yfinance", "000001", "2020-01-02"),
            ("openbb_yfinance", "000001", "2025-07-18"),
            ("baostock_history_k_data", "000001", "2022-01-04"),
            ("baostock_history_k_data", "000001", "2025-07-18"),
        ],
    )

    result = _bounds(conn, "000001")

    assert result == (pd.Timestamp("2022-01-04"), pd.Timestamp("2025-07-18"))
    conn.close()
