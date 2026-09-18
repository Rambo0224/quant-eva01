from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.akshare_public import (
    AkshareSourceUnavailable,
    fetch_sse_etf_scale,
    fetch_szse_etf_scale_history,
)
from macro_replay.db import ensure_db, insert_missing_etf_share_rows
from macro_replay.etf_catalog import ETF_WATCHLIST


SSE_SOURCE_ID = "akshare_etf_scale_sse"
SZSE_SOURCE_ID = "akshare_etf_scale_szse_daily"
ETF_BAR_SOURCE_ID = "akshare_etf_hist_em"
REPORT_PATH = Path("logs") / "etf_share_history_refresh.json"


def _write_report(report: dict) -> None:
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _watchlist_frame() -> pd.DataFrame:
    frame = pd.DataFrame(ETF_WATCHLIST)
    frame["code"] = frame["code"].astype("string").str.zfill(6)
    frame["exchange"] = frame["code"].map(lambda value: "SZSE" if str(value).startswith("159") else "SSE")
    return frame.drop_duplicates("code", keep="first").reset_index(drop=True)


def _trade_dates(start: str, end: str) -> list[pd.Timestamp]:
    calendar = Path("data") / "baostock_trade_dates.csv"
    if calendar.exists():
        frame = pd.read_csv(calendar)
        frame["date"] = pd.to_datetime(frame["calendar_date"], errors="coerce")
        frame["is_trading_day"] = pd.to_numeric(frame["is_trading_day"], errors="coerce").fillna(0).astype(int)
        dates = frame[
            frame["is_trading_day"].eq(1)
            & frame["date"].ge(pd.Timestamp(start))
            & frame["date"].le(pd.Timestamp(end))
        ]["date"]
        normalized = pd.DatetimeIndex(dates).normalize().drop_duplicates().sort_values()
        max_known = frame["date"].dropna().max()
        if pd.notna(max_known) and pd.Timestamp(end).normalize() > pd.Timestamp(max_known).normalize():
            extension_start = pd.Timestamp(max_known).normalize() + pd.Timedelta(days=1)
            extension = pd.bdate_range(start=extension_start, end=end)
            normalized = normalized.union(extension)
        return list(normalized)
    return list(pd.bdate_range(start=start, end=end))


def _existing_dates(conn, source_id: str, symbols=None) -> set[pd.Timestamp]:
    rows = conn.execute(
        """
        SELECT symbol,obs_time
        FROM etf_share_daily
        WHERE source_id = ?
        """,
        [source_id],
    ).fetchall()
    by_symbol = {}
    for symbol, day in rows:
        by_symbol.setdefault(str(symbol),set()).add(pd.Timestamp(day).normalize())
    wanted = set(symbols) if symbols is not None else set(by_symbol)
    # A single ETF on a date cannot certify the other ETFs on that date.
    if not wanted or any(symbol not in by_symbol for symbol in wanted):
        return set()
    days = set().union(*(by_symbol[symbol] for symbol in wanted))
    starts = {symbol:min(by_symbol[symbol]) for symbol in wanted}
    return {day for day in days if all(day < starts[symbol] or day in by_symbol[symbol] for symbol in wanted)}


def _existing_bounds(conn, source_id: str, symbol: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    row = conn.execute(
        """
        SELECT min(obs_time), max(obs_time)
        FROM etf_share_daily
        WHERE source_id = ? AND symbol = ?
        """,
        [source_id, str(symbol).zfill(6)],
    ).fetchone()
    return (
        pd.Timestamp(row[0]).normalize() if row and row[0] else None,
        pd.Timestamp(row[1]).normalize() if row and row[1] else None,
    )


def _fetch_one_sse_date(day: pd.Timestamp, watchlist: pd.DataFrame) -> tuple[pd.Timestamp, pd.DataFrame, str | None]:
    date_text = day.strftime("%Y%m%d")
    try:
        result = fetch_sse_etf_scale(date_text)
        frame = result.frame
        if frame.empty:
            return day, frame, None
        wanted = set(watchlist["code"].astype(str))
        frame = frame[frame["symbol"].astype(str).isin(wanted)].copy()
        if frame.empty:
            return day, frame, None
        frame = frame.merge(
            watchlist.rename(columns={"code": "symbol", "title": "catalog_name"})[["symbol", "catalog_name", "category"]],
            on="symbol",
            how="left",
        )
        frame["name"] = frame["catalog_name"].fillna(frame["name"])
        frame["extra"] = frame.apply(
            lambda row: {
                "source": SSE_SOURCE_ID,
                "etf_type": row.get("etf_type"),
                "raw_name": row.get("name"),
            },
            axis=1,
        )
        return day, frame[["symbol", "date", "name", "exchange", "category", "shares", "extra"]], None
    except Exception as exc:  # pragma: no cover - live retry path
        return day, pd.DataFrame(), str(exc)


def _fetch_szse_range(
    start: pd.Timestamp,
    end: pd.Timestamp,
    watchlist: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None]:
    try:
        result = fetch_szse_etf_scale_history(start.date().isoformat(), end.date().isoformat())
        frame = result.frame
        if frame.empty:
            return frame, None
        wanted = set(watchlist["code"].astype(str))
        frame = frame[frame["symbol"].astype(str).isin(wanted)].copy()
        if frame.empty:
            return frame, None
        frame = frame.merge(
            watchlist.rename(columns={"code": "symbol", "title": "catalog_name"})[["symbol", "catalog_name", "category"]],
            on="symbol",
            how="left",
        )
        frame["name"] = frame["catalog_name"].fillna(frame["name"])
        frame["extra"] = frame.apply(
            lambda row: {
                "source": SZSE_SOURCE_ID,
                "raw_name": row.get("name"),
            },
            axis=1,
        )
        return frame[["symbol", "date", "name", "exchange", "category", "shares", "extra"]], None
    except Exception as exc:  # pragma: no cover - live retry path
        return pd.DataFrame(), str(exc)


def _chunk_ranges(start: pd.Timestamp, end: pd.Timestamp, max_days: int = 120) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if start > end:
        return []
    ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    left = start.normalize()
    right = end.normalize()
    while left <= right:
        chunk_end = min(left + pd.Timedelta(days=max_days - 1), right)
        ranges.append((left, chunk_end))
        left = chunk_end + pd.Timedelta(days=1)
    return ranges


def _merge_ranges(ranges: list[tuple[pd.Timestamp, pd.Timestamp]]) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if not ranges:
        return []
    ordered = sorted((left.normalize(), right.normalize()) for left, right in ranges)
    merged: list[list[pd.Timestamp]] = [[ordered[0][0], ordered[0][1]]]
    for left, right in ordered[1:]:
        if left <= merged[-1][1] + pd.Timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return [(left, right) for left, right in merged]


def _missing_szse_ranges(
    conn,
    watchlist: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    needed: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for symbol in watchlist["code"].astype(str).tolist():
        earliest, latest = _existing_bounds(conn, SZSE_SOURCE_ID, symbol)
        if earliest is None or latest is None:
            needed.append((start, end))
            continue
        if start < earliest:
            needed.append((start, earliest - pd.Timedelta(days=1)))
        if end > latest:
            needed.append((latest + pd.Timedelta(days=1), end))
    merged = _merge_ranges(needed)
    chunked: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for left, right in merged:
        chunked.extend(_chunk_ranges(left, right))
    return chunked


def _recompute_share_changes(conn, source_id: str, symbols: list[str]) -> None:
    if not symbols:
        return
    symbols = [str(symbol).zfill(6) for symbol in symbols]
    placeholders = ", ".join("?" for _ in symbols)
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _etf_share_calc AS
        SELECT
            shares.source_id,
            shares.symbol,
            shares.obs_time,
            shares.shares - LAG(shares.shares) OVER (
                PARTITION BY shares.source_id, shares.symbol
                ORDER BY shares.obs_time
            ) AS share_change,
            bars.close AS close
        FROM etf_share_daily shares
        LEFT JOIN (
            SELECT symbol,obs_time,close FROM equity_daily_bars
            WHERE source_id IN (?, 'baostock_etf_history_k_data') AND close > 0
            QUALIFY row_number() OVER (PARTITION BY symbol,obs_time
                ORDER BY CASE source_id WHEN 'akshare_etf_hist_em' THEN 0 ELSE 1 END,ingested_at DESC)=1
        ) bars
          ON bars.symbol = shares.symbol
         AND bars.obs_time = shares.obs_time
        WHERE shares.source_id = ?
          AND shares.symbol IN ({placeholders})
        """,
        [ETF_BAR_SOURCE_ID, source_id, *symbols],
    )
    conn.execute(
        """
        UPDATE etf_share_daily target
        SET share_change = calc.share_change,
            close = calc.close,
            estimated_flow_amount = calc.share_change * calc.close
        FROM _etf_share_calc calc
        WHERE target.source_id = calc.source_id
          AND target.symbol = calc.symbol
          AND target.obs_time = calc.obs_time
        """
    )
    conn.execute("DROP TABLE IF EXISTS _etf_share_calc")


def refresh(
    start: str = "2021-01-01",
    end: str | None = None,
    symbols: list[str] | None = None,
    max_workers: int = 5,
    batch_size: int = 60,
) -> dict:
    end = end or pd.Timestamp.today().date().isoformat()
    report: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_id": "akshare_etf_scale_sse+akshare_etf_scale_szse_daily",
        "success": [],
        "failed": [],
        "skipped": [],
    }
    watchlist = _watchlist_frame()
    if symbols:
        wanted_symbols = {str(symbol).zfill(6) for symbol in symbols}
        watchlist = watchlist[watchlist["code"].isin(wanted_symbols)].copy()
    report["watchlist_count"] = int(len(watchlist))
    if watchlist.empty:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        return report

    conn = ensure_db()
    try:
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()

        sse_watchlist = watchlist[watchlist["exchange"].eq("SSE")].copy()
        szse_watchlist = watchlist[watchlist["exchange"].eq("SZSE")].copy()

        sse_existing = _existing_dates(conn, SSE_SOURCE_ID, sse_watchlist['code'].astype(str)) if not sse_watchlist.empty else set()
        sse_dates = [day for day in _trade_dates(start, end) if day.normalize() not in sse_existing] if not sse_watchlist.empty else []
        szse_ranges = _missing_szse_ranges(conn, szse_watchlist, start_ts, end_ts) if not szse_watchlist.empty else []

        report["requested_dates"] = len(sse_dates)
        report["requested_ranges"] = [
            {"start": left.date().isoformat(), "end": right.date().isoformat()}
            for left, right in szse_ranges
        ]
        completed = 0
        inserted_total = 0
        successful_dates = 0
        successful_ranges = 0
        affected_symbols: set[str] = set()
        inserted_date_min: pd.Timestamp | None = None
        inserted_date_max: pd.Timestamp | None = None
        report["status"] = "running"
        report["completed_dates"] = 0
        report["completed_ranges"] = 0
        report["inserted_rows"] = 0
        _write_report(report)
        if not sse_watchlist.empty:
            for offset in range(0, len(sse_dates), max(1, batch_size)):
                batch = sse_dates[offset : offset + max(1, batch_size)]
                batch_frames: list[pd.DataFrame] = []
                with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
                    futures = {executor.submit(_fetch_one_sse_date, day, sse_watchlist): day for day in batch}
                    for future in as_completed(futures):
                        day, frame, error = future.result()
                        if error:
                            report["failed"].append({"source_id": SSE_SOURCE_ID, "date": day.date().isoformat(), "error": error})
                        elif frame.empty:
                            report["skipped"].append({"source_id": SSE_SOURCE_ID, "date": day.date().isoformat(), "reason": "no watchlist ETF rows from SSE scale table"})
                        else:
                            batch_frames.append(frame)
                            successful_dates += 1
                        completed += 1

                if batch_frames:
                    combined = pd.concat(batch_frames, ignore_index=True).drop_duplicates(["symbol", "date"], keep="last")
                    inserted_total += insert_missing_etf_share_rows(conn, SSE_SOURCE_ID, combined)
                    batch_symbols = set(combined["symbol"].dropna().astype(str).unique().tolist())
                    affected_symbols.update(batch_symbols)
                    _recompute_share_changes(conn, SSE_SOURCE_ID, sorted(batch_symbols))
                    batch_min = combined["date"].min()
                    batch_max = combined["date"].max()
                    inserted_date_min = batch_min if inserted_date_min is None else min(inserted_date_min, batch_min)
                    inserted_date_max = batch_max if inserted_date_max is None else max(inserted_date_max, batch_max)

                report["completed_dates"] = completed
                report["successful_dates"] = successful_dates
                report["inserted_rows"] = inserted_total
                report["affected_symbols"] = len(affected_symbols)
                _write_report(report)
                if completed % (batch_size * 2) == 0 or completed == len(sse_dates):
                    print(f"[INFO] ETF share SSE progress {completed}/{len(sse_dates)}", flush=True)
                time.sleep(0.2)

        if not szse_watchlist.empty:
            for index, (left, right) in enumerate(szse_ranges, start=1):
                frame, error = _fetch_szse_range(left, right, szse_watchlist)
                if error:
                    report["failed"].append(
                        {
                            "source_id": SZSE_SOURCE_ID,
                            "start": left.date().isoformat(),
                            "end": right.date().isoformat(),
                            "error": error,
                        }
                    )
                elif frame.empty:
                    report["skipped"].append(
                        {
                            "source_id": SZSE_SOURCE_ID,
                            "start": left.date().isoformat(),
                            "end": right.date().isoformat(),
                            "reason": "no watchlist ETF rows from SZSE daily scale table",
                        }
                    )
                else:
                    inserted_total += insert_missing_etf_share_rows(conn, SZSE_SOURCE_ID, frame)
                    range_symbols = set(frame["symbol"].dropna().astype(str).unique().tolist())
                    affected_symbols.update(range_symbols)
                    _recompute_share_changes(conn, SZSE_SOURCE_ID, sorted(range_symbols))
                    successful_ranges += 1
                    batch_min = frame["date"].min()
                    batch_max = frame["date"].max()
                    inserted_date_min = batch_min if inserted_date_min is None else min(inserted_date_min, batch_min)
                    inserted_date_max = batch_max if inserted_date_max is None else max(inserted_date_max, batch_max)

                report["completed_ranges"] = index
                report["successful_ranges"] = successful_ranges
                report["inserted_rows"] = inserted_total
                report["affected_symbols"] = len(affected_symbols)
                _write_report(report)
                if index % 5 == 0 or index == len(szse_ranges):
                    print(f"[INFO] ETF share SZSE progress {index}/{len(szse_ranges)}", flush=True)
                time.sleep(0.2)

        if affected_symbols:
            summary = conn.execute(
                """
                SELECT source_id, symbol, any_value(name) AS name, any_value(category) AS category,
                       count(*) AS row_count, min(cast(obs_time AS date)) AS min_date,
                       max(cast(obs_time AS date)) AS max_date
                FROM etf_share_daily
                WHERE source_id IN (?, ?)
                GROUP BY source_id, symbol
                ORDER BY source_id, symbol
                """,
                [SSE_SOURCE_ID, SZSE_SOURCE_ID],
            ).df()
            report["success"].append({
                "inserted_rows": inserted_total,
                "successful_dates": successful_dates,
                "successful_ranges": successful_ranges,
                "symbols": len(affected_symbols),
                "date_min": inserted_date_min.date().isoformat() if inserted_date_min is not None else None,
                "date_max": inserted_date_max.date().isoformat() if inserted_date_max is not None else None,
            })
            report["summary"] = json.loads(summary.to_json(orient="records", force_ascii=False, date_format="iso"))
    finally:
        conn.close()

    report["status"] = "completed"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_report(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch ETF share-count history and compute daily share changes.")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=pd.Timestamp.today().date().isoformat())
    parser.add_argument("--symbols", default="", help="Optional comma-separated ETF codes")
    parser.add_argument("--max-workers", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=60)
    args = parser.parse_args()
    symbols = [value.strip() for value in args.symbols.split(",") if value.strip()] or None
    print(json.dumps(refresh(args.start, args.end, symbols, args.max_workers, args.batch_size), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
