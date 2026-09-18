from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.cffex import CffexSourceUnavailable, fetch_cffex_history, main_contract_frame, select_main_contracts
from datahub.adapters.akshare_public import fetch_trade_dates
from datahub.transforms.ashare import annualized_volatility
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, insert_missing_observations, record_chart


INDEX_MAP = {"IF": ("if_close", "hs300-close"), "IC": ("ic_close", "zz500-close"), "IH": ("ih_close", "sz50-close")}
DERIVED_MAP = {"IF": ("if-vol-20d", "if_basis"), "IC": ("ic-vol-20d", "ic_basis")}


def _indicator_by_id(indicator_id: str):
    for indicator in load_indicators():
        if indicator.id == indicator_id:
            return indicator
    raise KeyError(f"Unknown indicator: {indicator_id}")


def _store(conn, indicator, frame: pd.DataFrame, field: str, source_id: str, start: str, note: str) -> dict:
    rows = []
    for _, row in frame[["date", field]].dropna().iterrows():
        rows.append((pd.Timestamp(row["date"]).to_pydatetime(), float(row[field]), {"note": note}))
    if not rows:
        return {"indicator_id": indicator.id, "rows": 0}
    inserted = insert_missing_observations(
        conn,
        indicator.id,
        f"{indicator.id}:{field}",
        source_id,
        rows,
        unit=(indicator.chart or {}).get("unit"),
    )
    stored = conn.execute(
        """
        SELECT obs_time AS date, value AS value
        FROM observations
        WHERE indicator_id = ? AND series_id = ?
        ORDER BY obs_time
        """,
        [indicator.id, f"{indicator.id}:{field}"],
    ).df().rename(columns={"value": field})
    chart = dict(indicator.chart or {})
    chart["source_label"] = source_id
    html_path, image_path = render_chart(stored[["date", field]], replace(indicator, chart=chart), Path("charts") / indicator.id)
    record_chart(conn, indicator.id, html_path, image_path, f"{source_id} / {note}")
    return {
        "indicator_id": indicator.id,
        "rows": len(stored),
        "inserted": inserted,
        "date_min": str(stored["date"].min().date()),
        "date_max": str(stored["date"].max().date()),
        "html_path": str(html_path),
        "image_path": str(image_path),
    }


def _series_dates(conn, indicator_id: str, field: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    row = conn.execute(
        "SELECT min(obs_time), max(obs_time) FROM observations WHERE indicator_id = ? AND series_id = ?",
        [indicator_id, f"{indicator_id}:{field}"],
    ).fetchone()
    return (
        pd.Timestamp(row[0]).normalize() if row and row[0] else None,
        pd.Timestamp(row[1]).normalize() if row and row[1] else None,
    )


def _missing_ranges(conn, start: str, end: str) -> list[tuple[str, str]]:
    requested_start = pd.Timestamp(start).normalize()
    requested_end = pd.Timestamp(end).normalize()
    trade_dates = fetch_trade_dates(start, end)
    if trade_dates.empty:
        return []
    first_trade_date = trade_dates.min()
    last_trade_date = trade_dates.max()
    ranges: list[tuple[str, str]] = []
    for indicator_id, field in (("if-close", "if_close"), ("ic-close", "ic_close"), ("ih-close", "ih_close")):
        earliest, latest = _series_dates(conn, indicator_id, field)
        if earliest is None or latest is None:
            ranges.append((requested_start.date().isoformat(), requested_end.date().isoformat()))
            continue
        if first_trade_date < earliest:
            ranges.append((first_trade_date.date().isoformat(), min(last_trade_date, earliest - pd.Timedelta(days=1)).date().isoformat()))
        if last_trade_date > latest:
            ranges.append((max(first_trade_date, latest + pd.Timedelta(days=1)).date().isoformat(), last_trade_date.date().isoformat()))
    return sorted(set(ranges))


def refresh(start: str, end: str, workers: int = 6, batch_days: int = 20) -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}

    conn = ensure_db()
    ranges = _missing_ranges(conn, start, end)
    conn.close()
    if not ranges:
        report["skipped"].append({"reason": "requested CFFEX boundary dates already exist in database"})
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        Path("logs").mkdir(exist_ok=True)
        Path("logs/cffex_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    histories = []
    for range_start, range_end in ranges:
        try:
            histories.append(fetch_cffex_history(range_start, range_end, batch_days=batch_days))
        except CffexSourceUnavailable as exc:
            report["failed"].append({"endpoint": "get_futures_daily", "start": range_start, "end": range_end, "error": str(exc)})

    if not histories:
        report["skipped"].append({"reason": "CFFEX returned no ingestible daily rows; existing observations were preserved"})
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        Path("logs").mkdir(exist_ok=True)
        Path("logs/cffex_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    history_frame = pd.concat([result.frame for result in histories], ignore_index=True).drop_duplicates(["symbol", "date"])
    selected = select_main_contracts(history_frame)
    wide = main_contract_frame(selected)
    conn = ensure_db()
    try:
        for variety, (factor, _) in INDEX_MAP.items():
            frame = wide[["date", factor]].dropna()
            if not frame.empty:
                report["success"].append(_store(conn, _indicator_by_id(f"{factor.replace('_close', '')}-close"), frame, factor, "cffex_official", start, f"daily dominant {variety} contract"))

        for variety, (vol_id, basis_field) in DERIVED_MAP.items():
            factor = INDEX_MAP[variety][0]
            close = wide[["date", factor]].dropna().copy()
            close[vol_id.replace("-", "_")] = annualized_volatility(close[factor], window=20)
            vol_field = vol_id.replace("-", "_")
            vol_frame = close[["date", vol_field]].dropna()
            if not vol_frame.empty:
                report["success"].append(_store(conn, _indicator_by_id(vol_id), vol_frame, vol_field, "derived", start, "annualized 20-day volatility of daily dominant contract"))

            benchmark_factor = INDEX_MAP[variety][1]
            benchmark_id = benchmark_factor
            benchmark = pd.read_sql_query(
                "SELECT obs_time AS date, value FROM observations WHERE indicator_id = ? ORDER BY obs_time",
                conn,
                params=[benchmark_id],
            )
            basis = close.merge(benchmark, on="date", how="inner")
            if not basis.empty:
                basis[basis_field] = basis[factor] - basis["value"]
                basis_frame = basis[["date", basis_field]].dropna()
                report["success"].append(_store(conn, _indicator_by_id(basis_field.replace("_", "-")), basis_frame, basis_field, "derived", start, f"{variety} dominant close minus benchmark index close"))

        report["skipped"].append({"indicator_id": "if-basis-annual", "reason": "daily dominant contract data has no stable expiry-date field; annualization is intentionally withheld"})
        report["skipped"].append({"indicator_id": "ic-basis-annual", "reason": "daily dominant contract data has no stable expiry-date field; annualization is intentionally withheld"})
    finally:
        conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/cffex_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh CFFEX dominant index-futures indicators")
    parser.add_argument("--start", default=(pd.Timestamp.today() - pd.Timedelta(days=45)).date().isoformat())
    parser.add_argument("--end", default=pd.Timestamp.today().date().isoformat())
    parser.add_argument("--workers", type=int, default=6, help="Concurrent official daily requests")
    parser.add_argument("--batch-days", type=int, default=20, help="Maximum calendar days per AkShare range request")
    args = parser.parse_args()
    print(json.dumps(refresh(args.start, args.end, args.workers, args.batch_days), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
