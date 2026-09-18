from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.akshare_public import AkshareSourceUnavailable, fetch_limit_pool_counts
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since


FACTORS = ("limit_up_count", "limit_down_count", "explosive_ratio")


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"No catalog indicator found for {factor}")


def _store_factor(conn, factor: str, frame: pd.DataFrame, start_date: str, source_id: str, endpoint: str) -> dict:
    indicator = _indicator_by_factor(factor)
    rows = [
        (
            pd.Timestamp(row["date"]).to_pydatetime(),
            float(row[factor]),
            {"endpoint": endpoint, "note": "AkShare limit-pool fallback"},
        )
        for _, row in frame[["date", factor]].dropna().iterrows()
    ]
    replace_observations_since(
        conn,
        indicator.id,
        f"{indicator.id}:{factor}",
        source_id,
        rows,
        since=pd.Timestamp(start_date).to_pydatetime(),
        unit=(indicator.chart or {}).get("unit"),
    )
    chart = dict(indicator.chart or {})
    chart["source_label"] = source_id
    chart_indicator = replace(indicator, chart=chart)
    html_path, image_path = render_chart(frame[["date", factor]], chart_indicator, Path("charts") / indicator.id)
    record_chart(conn, indicator.id, html_path, image_path, f"{source_id.upper()} / {endpoint}")
    return {"factor": factor, "rows": len(rows), "date_min": str(frame["date"].min().date()), "date_max": str(frame["date"].max().date())}


def refresh(start_date: str, end_date: str) -> dict:
    dates = pd.bdate_range(start_date, end_date)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "skipped": [], "failed": []}
    frames: list[pd.DataFrame] = []
    for current in dates:
        date_text = current.strftime("%Y%m%d")
        try:
            result = fetch_limit_pool_counts(date_text)
            frames.append(result.frame)
        except AkshareSourceUnavailable as exc:
            report["skipped"].append({"date": date_text, "reason": str(exc)})
        except Exception as exc:
            report["failed"].append({"date": date_text, "error": str(exc)})
        time.sleep(0.15)

    if frames:
        combined = pd.concat(frames, ignore_index=True).sort_values("date").drop_duplicates("date", keep="last")
        conn = ensure_db()
        try:
            for factor in FACTORS:
                report["success"].append(_store_factor(conn, factor, combined, start_date, "akshare_public", "limit_pool_daily"))
        finally:
            conn.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/limit_pool_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-04-01")
    parser.add_argument("--end", default="2026-07-16")
    args = parser.parse_args()
    print(json.dumps(refresh(args.start, args.end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
