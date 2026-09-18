from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.akshare_equity import fetch_a_share_universe
from datahub.adapters.openbb_yfinance import normalize_openbb_symbol
from datahub.adapters.yfinance_batch import SOURCE_ID, fetch_histories
from macro_replay.db import ensure_db, insert_missing_equity_bars


ALL_SOURCE_IDS = ("akshare_stock_zh_a_hist_qfq", "baostock_history_k_data", "openbb_yfinance", SOURCE_ID)
NEW_LISTING_PREFIXES = ("001", "301", "688", "689", "8", "920")


def _all_bounds(conn) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    placeholders = ", ".join("?" for _ in ALL_SOURCE_IDS)
    rows = conn.execute(
        f"""
        SELECT symbol, min(obs_time), max(obs_time)
        FROM equity_daily_bars
        WHERE source_id IN ({placeholders})
        GROUP BY symbol
        """,
        list(ALL_SOURCE_IDS),
    ).fetchall()
    bounds = {}
    for symbol, left, right in rows:
        if left is None or right is None:
            continue
        normalized = str(symbol).split(".", 1)[0].zfill(6)
        current = bounds.get(normalized)
        left_ts, right_ts = pd.Timestamp(left).normalize(), pd.Timestamp(right).normalize()
        bounds[normalized] = (
            min(left_ts, current[0]) if current else left_ts,
            max(right_ts, current[1]) if current else right_ts,
        )
    return bounds


def refresh(start: str, end: str, batch_size: int = 50, max_symbols: int | None = None, symbols: list[str] | None = None) -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "source_id": SOURCE_ID, "success": [], "failed": [], "skipped": []}
    conn = ensure_db()
    try:
        universe = fetch_a_share_universe()
        symbols = [str(value).zfill(6) for value in (symbols or universe["code"].astype(str).str.zfill(6).tolist())]
        if max_symbols is not None:
            symbols = symbols[:max_symbols]
        start_ts, end_ts = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        bounds_by_symbol = _all_bounds(conn)
        groups: dict[tuple[pd.Timestamp, pd.Timestamp], list[str]] = defaultdict(list)
        for symbol in symbols:
            bounds = bounds_by_symbol.get(symbol)
            if bounds is None:
                groups[(start_ts, end_ts)].append(symbol)
            elif start_ts < bounds[0] or end_ts > bounds[1]:
                has_prelisting_gap = start_ts < bounds[0] and not symbol.startswith(NEW_LISTING_PREFIXES)
                if not has_prelisting_gap and end_ts <= bounds[1]:
                    report["skipped"].append({"symbol": symbol, "reason": "pre-listing history is unavailable"})
                    continue
                left = start_ts if has_prelisting_gap else bounds[1] + pd.Timedelta(days=1)
                right = bounds[0] - pd.Timedelta(days=1) if has_prelisting_gap else end_ts
                # A one-day boundary such as 2020-01-01 is a market holiday;
                # do not send it to Yahoo as a history request.
                if left <= right and (right - left).days >= 3:
                    groups[(left, right)].append(symbol)
                elif left <= right:
                    report["skipped"].append({"symbol": symbol, "reason": "only a non-trading boundary gap remains"})
            else:
                report["skipped"].append({"symbol": symbol, "reason": "requested date bounds already exist"})

        total = sum(len(items) for items in groups.values())
        completed = 0
        for (left, right), group in groups.items():
            for offset in range(0, len(group), max(1, batch_size)):
                batch = group[offset : offset + max(1, batch_size)]
                tickers = [normalize_openbb_symbol(symbol) for symbol in batch]
                try:
                    downloaded = fetch_histories(tickers, left.date().isoformat(), right.date().isoformat())
                except Exception as exc:
                    report["failed"].append({"symbols": batch, "error": str(exc)})
                    completed += len(batch)
                    continue
                for symbol in batch:
                    ticker = normalize_openbb_symbol(symbol)
                    frame = downloaded.get(ticker)
                    if frame is None or frame.empty:
                        report["failed"].append({"symbol": symbol, "error": "Yahoo batch returned no usable rows"})
                    else:
                        inserted = insert_missing_equity_bars(conn, SOURCE_ID, frame)
                        report["success"].append({"symbol": symbol, "fetched_rows": len(frame), "inserted_rows": inserted})
                    completed += 1
                print(f"[INFO] Yahoo batch history progress {completed}/{total}", flush=True)
    finally:
        conn.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["universe_rows"] = len(universe) if "universe" in locals() else 0
    report["requested_symbols"] = len(symbols) if "symbols" in locals() else 0
    Path("logs").mkdir(exist_ok=True)
    Path("logs/yfinance_batch_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch A-share daily bars in bounded Yahoo Finance batches")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=(pd.Timestamp.today() - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--symbols", default="", help="Optional comma-separated six-digit symbols")
    args = parser.parse_args()
    symbols = [value.strip() for value in args.symbols.split(",") if value.strip()] or None
    print(json.dumps(refresh(args.start, args.end, args.batch_size, args.max_symbols, symbols), ensure_ascii=False, indent=2))
