from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.openbb_yfinance import fetch_equity_history as fetch_openbb_history, normalize_openbb_symbol
from datahub.adapters.akshare_public import fetch_trade_dates
from datahub.adapters.tushare_pro import fetch_equity_history as fetch_tushare_history
from macro_replay.db import ensure_db, insert_missing_equity_bars


def _existing_bounds(conn, source_id: str, symbol: str):
    row = conn.execute(
        """
        SELECT min(obs_time), max(obs_time)
        FROM equity_daily_bars
        WHERE source_id = ? AND symbol = ?
        """,
        [source_id, symbol],
    ).fetchone()
    return (
        pd.Timestamp(row[0]).normalize() if row and row[0] else None,
        pd.Timestamp(row[1]).normalize() if row and row[1] else None,
    )


def _ranges_to_fetch(conn, source_id: str, symbol: str, start: str, end: str, trade_dates=None):
    requested_start = pd.Timestamp(start).normalize()
    requested_end = pd.Timestamp(end).normalize()
    if trade_dates is not None and len(trade_dates):
        requested_start = trade_dates.min()
        requested_end = trade_dates.max()
    earliest, latest = _existing_bounds(conn, source_id, symbol)
    if earliest is None or latest is None:
        return [(requested_start, requested_end)]
    ranges = []
    if requested_start < earliest:
        ranges.append((requested_start, min(requested_end, earliest - pd.Timedelta(days=1))))
    if requested_end > latest:
        ranges.append((max(requested_start, latest + pd.Timedelta(days=1)), requested_end))
    return [(left, right) for left, right in ranges if left <= right]


def refresh(
    symbols: list[str],
    start: str,
    end: str,
    source: str = "openbb",
    token: str | None = None,
) -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    source_id = "openbb_yfinance" if source == "openbb" else "tushare_pro"
    fetcher = fetch_openbb_history if source == "openbb" else fetch_tushare_history
    conn = ensure_db()
    try:
        trade_dates = fetch_trade_dates(start, end)
        for symbol in symbols:
            storage_symbol = normalize_openbb_symbol(symbol) if source == "openbb" else symbol
            try:
                ranges = _ranges_to_fetch(conn, source_id, storage_symbol, start, end, trade_dates)
                if not ranges:
                    report["skipped"].append({"symbol": symbol, "reason": "requested trading-date boundaries already exist"})
                    continue
                inserted = 0
                fetched_rows = 0
                for left, right in ranges:
                    kwargs = {"symbol" if source == "openbb" else "ts_code": symbol, "start_date": left.date().isoformat(), "end_date": right.date().isoformat()}
                    if source == "tushare":
                        kwargs.update({"token": token})
                    result = fetcher(**kwargs)
                    fetched_rows += len(result.frame)
                    inserted += insert_missing_equity_bars(conn, result.source_id, result.frame)
                report["success"].append({"symbol": symbol, "source": source_id, "fetched_rows": fetched_rows, "inserted": inserted})
            except Exception as exc:
                report["failed"].append({"symbol": symbol, "source": source_id, "error": str(exc)})
    except Exception as exc:
        report["failed"].append({"source": source_id, "error": f"calendar or database setup failed: {exc}"})
    finally:
        conn.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/equity_bars_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Incrementally fetch raw A-share daily bars")
    parser.add_argument("--symbols", required=True, help="Comma-separated codes, e.g. 600000,000001")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--source", choices=("openbb", "tushare"), default="openbb")
    parser.add_argument("--token", default=None, help="Tushare Pro token; otherwise TUSHARE_TOKEN is used")
    args = parser.parse_args()
    print(json.dumps(refresh([item.strip() for item in args.symbols.split(",") if item.strip()], args.start, args.end, args.source, args.token), ensure_ascii=False, indent=2))
