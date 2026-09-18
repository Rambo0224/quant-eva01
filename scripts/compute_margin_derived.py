from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import DB_PATH, ensure_db, record_chart, replace_observations_since


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"No catalog indicator found for {factor}")


def compute() -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": []}
    source_conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        base_indicator = _indicator_by_factor("short_balance")
        rows = source_conn.execute(
            """
            SELECT obs_time, value
            FROM observations
            WHERE indicator_id = ? AND series_id = ?
            QUALIFY row_number() OVER (PARTITION BY obs_time ORDER BY ingested_at DESC,source_id)=1
            ORDER BY obs_time
            """,
            [base_indicator.id, f"{base_indicator.id}:short_balance"],
        ).fetchall()
    finally:
        source_conn.close()

    if not rows:
        report["failed"].append({"factor": "short_increase_rate", "error": "no short_balance observations"})
        return report

    frame = pd.DataFrame(rows, columns=["date", "short_balance"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["short_increase_rate"] = frame["short_balance"].pct_change()
    frame = frame.replace([float("inf"), float("-inf")], pd.NA).dropna(subset=["short_increase_rate"])

    derived_indicator = _indicator_by_factor("short_increase_rate")
    target_conn = ensure_db()
    try:
        stored = [
            (
                row["date"].to_pydatetime(),
                float(row["short_increase_rate"]),
                {"calculation": "short_balance.pct_change()", "base_series": "short_balance"},
            )
            for _, row in frame.iterrows()
        ]
        replace_observations_since(
            target_conn,
            derived_indicator.id,
            f"{derived_indicator.id}:short_increase_rate",
            "derived",
            stored,
            since=frame["date"].min().to_pydatetime(),
            unit=(derived_indicator.chart or {}).get("unit"),
        )
        chart = dict(derived_indicator.chart or {})
        chart["source_label"] = "derived"
        chart_indicator = replace(derived_indicator, chart=chart)
        html_path, image_path = render_chart(
            frame[["date", "short_increase_rate"]],
            chart_indicator,
            Path("charts") / derived_indicator.id,
        )
        record_chart(target_conn, derived_indicator.id, html_path, image_path, "DERIVED / short_balance.pct_change()")
        report["success"].append(
            {
                "factor": "short_increase_rate",
                "rows": len(stored),
                "date_min": str(frame["date"].min().date()),
                "date_max": str(frame["date"].max().date()),
            }
        )
    finally:
        target_conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/margin_derived_refresh.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    print(json.dumps(compute(), ensure_ascii=False, indent=2))
