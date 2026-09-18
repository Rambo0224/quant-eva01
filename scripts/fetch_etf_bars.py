from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.akshare_public import AkshareSourceUnavailable, fetch_etf_history
from datahub.adapters.baostock_equity import BaoStockClient, BaoStockUnavailable
from macro_replay.db import ensure_db, insert_missing_equity_bars
from macro_replay.etf_catalog import ETF_WATCHLIST


SOURCE_ID = "akshare_etf_hist_em"
BAOSTOCK_SOURCE_ID = "baostock_etf_history_k_data"


def _watchlist_frame() -> pd.DataFrame:
    frame = pd.DataFrame(ETF_WATCHLIST)
    frame["code"] = frame["code"].astype("string").str.zfill(6)
    return frame.drop_duplicates("code", keep="first").reset_index(drop=True)


def _existing_bounds(conn, symbol: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    row = conn.execute(
        """
        SELECT min(obs_time), max(obs_time)
        FROM equity_daily_bars
        WHERE source_id IN (?, ?) AND symbol = ?
        """,
        [SOURCE_ID, BAOSTOCK_SOURCE_ID, str(symbol).zfill(6)],
    ).fetchone()
    return (
        pd.Timestamp(row[0]).normalize() if row and row[0] else None,
        pd.Timestamp(row[1]).normalize() if row and row[1] else None,
    )


def _requested_range(
    conn,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    backfill_history: bool = False,
) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    earliest, latest = _existing_bounds(conn, symbol)
    if earliest is None or latest is None:
        return start, end
    if backfill_history and start < earliest:
        return start, earliest - pd.Timedelta(days=1)
    if end > latest:
        return latest + pd.Timedelta(days=1), end
    return None


def _attach_metadata(frame: pd.DataFrame, name: str, category: str) -> pd.DataFrame:
    result = frame.copy()
    result["extra"] = [
        {
            "asset_type": "ETF",
            "name": name,
            "category": category,
            "source": SOURCE_ID,
        }
        for _ in range(len(result))
    ]
    return result


def _baostock_code(symbol: str) -> str:
    code = str(symbol).zfill(6)
    exchange = "sz" if code.startswith("159") else "sh"
    return f"{exchange}.{code}"


def refresh(
    start: str = "2010-01-01",
    end: str | None = None,
    symbols: list[str] | None = None,
    max_workers: int = 3,
    adjust: str = "",
    backfill_history: bool = False,
) -> dict:
    started_at = datetime.now(timezone.utc)
    end = end or pd.Timestamp.today().date().isoformat()
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    report: dict = {
        "started_at": started_at.isoformat(),
        "source_id": SOURCE_ID,
        "success": [],
        "failed": [],
        "skipped": [],
    }

    watchlist = _watchlist_frame()
    if symbols:
        requested_codes = {str(symbol).zfill(6) for symbol in symbols}
        watchlist = watchlist[watchlist["code"].isin(requested_codes)].copy()
    report["watchlist_count"] = int(len(watchlist))

    conn = ensure_db()
    try:
        tasks: list[tuple[str, str, str, pd.Timestamp, pd.Timestamp]] = []
        for row in watchlist.itertuples(index=False):
            earliest, latest = _existing_bounds(conn, row.code)
            if earliest is not None and start_ts < earliest and not backfill_history:
                report["skipped"].append({
                    "symbol": row.code,
                    "name": row.title,
                    "reason": "older history gap is not backfilled during startup incremental refresh",
                    "requested_start": start_ts.date().isoformat(),
                    "existing_start": earliest.date().isoformat(),
                })
            date_range = _requested_range(conn, row.code, start_ts, end_ts, backfill_history=backfill_history)
            if date_range is None:
                report["skipped"].append({
                    "symbol": row.code,
                    "name": row.title,
                    "reason": "requested date bounds already exist in database",
                })
                continue
            tasks.append((row.code, row.title, row.category, date_range[0], date_range[1]))

        def fetch_one_akshare(task: tuple[str, str, str, pd.Timestamp, pd.Timestamp]):
            symbol, name, category, left, right = task
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    result = fetch_etf_history(
                        symbol=symbol,
                        start_date=left.date().isoformat(),
                        end_date=right.date().isoformat(),
                        adjust=adjust,
                    )
                    return result, name, category, left, right
                except Exception as exc:  # pragma: no cover - live retry path
                    last_error = exc
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
            raise AkshareSourceUnavailable(f"ETF history failed after retries: {last_error}")

        completed = 0
        baostock_failed_tasks: list[tuple[str, str, str, pd.Timestamp, pd.Timestamp, str]] = []
        try:
            with BaoStockClient() as client:
                for task in tasks:
                    symbol, name, category, left, right = task
                    try:
                        result = client.fetch_history(
                            _baostock_code(symbol),
                            left.date().isoformat(),
                            right.date().isoformat(),
                        )
                        frame = _attach_metadata(result.frame, name=name, category=category)
                        inserted = insert_missing_equity_bars(conn, BAOSTOCK_SOURCE_ID, frame)
                        report["success"].append({
                            "symbol": symbol,
                            "name": name,
                            "category": category,
                            "source_id": BAOSTOCK_SOURCE_ID,
                            "requested_start": left.date().isoformat(),
                            "requested_end": right.date().isoformat(),
                            "fetched_rows": len(frame),
                            "inserted_rows": inserted,
                            "date_min": frame["date"].min().date().isoformat() if not frame.empty else None,
                            "date_max": frame["date"].max().date().isoformat() if not frame.empty else None,
                        })
                    except Exception as exc:
                        baostock_failed_tasks.append((*task, str(exc)))
                    completed += 1
                    if completed % 10 == 0 or completed == len(tasks):
                        print(f"[INFO] ETF history BaoStock progress {completed}/{len(tasks)}", flush=True)
        except BaoStockUnavailable as exc:
            baostock_failed_tasks = [(*task, str(exc)) for task in tasks]

        fallback_tasks = [task[:5] for task in baostock_failed_tasks]
        if fallback_tasks:
            fallback_completed = 0
            with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
                futures = {executor.submit(fetch_one_akshare, task): task for task in fallback_tasks}
                for future in as_completed(futures):
                    symbol, name, category, left, right = futures[future]
                    original_error = next(
                        (error for failed_symbol, _, _, _, _, error in baostock_failed_tasks if failed_symbol == symbol),
                        "",
                    )
                    try:
                        result, name, category, left, right = future.result()
                        frame = _attach_metadata(result.frame, name=name, category=category)
                        inserted = insert_missing_equity_bars(conn, SOURCE_ID, frame)
                        report["success"].append({
                            "symbol": symbol,
                            "name": name,
                            "category": category,
                            "source_id": SOURCE_ID,
                            "requested_start": left.date().isoformat(),
                            "requested_end": right.date().isoformat(),
                            "fetched_rows": len(frame),
                            "inserted_rows": inserted,
                            "date_min": frame["date"].min().date().isoformat() if not frame.empty else None,
                            "date_max": frame["date"].max().date().isoformat() if not frame.empty else None,
                            "fallback_from": BAOSTOCK_SOURCE_ID,
                        })
                    except Exception as exc:
                        report["failed"].append({
                            "symbol": symbol,
                            "name": name,
                            "category": category,
                            "requested_start": left.date().isoformat(),
                            "requested_end": right.date().isoformat(),
                            "error": f"BaoStock failed: {original_error}; AkShare fallback failed: {exc}",
                        })
                    fallback_completed += 1
                    if fallback_completed % 10 == 0 or fallback_completed == len(fallback_tasks):
                        print(f"[INFO] ETF history AkShare fallback progress {fallback_completed}/{len(fallback_tasks)}", flush=True)
    finally:
        conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/etf_bars_refresh.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch broad-market and industry ETF daily OHLCV into DuckDB.")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default=pd.Timestamp.today().date().isoformat())
    parser.add_argument("--symbols", default="", help="Optional comma-separated ETF codes")
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--adjust", choices=("", "qfq", "hfq"), default="")
    parser.add_argument("--backfill-history", action="store_true")
    args = parser.parse_args()
    symbols = [value.strip() for value in args.symbols.split(",") if value.strip()] or None
    print(json.dumps(refresh(args.start, args.end, symbols, args.max_workers, args.adjust, args.backfill_history), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
