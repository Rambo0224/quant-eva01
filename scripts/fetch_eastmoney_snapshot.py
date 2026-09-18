from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.eastmoney_market import fetch_a_share_snapshot
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"Unknown factor: {factor}")


def _store_scalar(conn, factor: str, date: pd.Timestamp, value: float, note: str) -> dict:
    indicator = _indicator_by_factor(factor)
    replace_observations_since(
        conn,
        indicator.id,
        f"{indicator.id}:{factor}",
        "eastmoney_public",
        [(date.to_pydatetime(), float(value), {"note": note})],
        since=date.to_pydatetime(),
        unit=(indicator.chart or {}).get("unit"),
    )
    chart = dict(indicator.chart or {})
    chart["source_label"] = "eastmoney_public"
    html_path, image_path = render_chart(
        pd.DataFrame({"date": [date], factor: [float(value)]}),
        replace(indicator, chart=chart),
        PROJECT_ROOT / "charts" / indicator.id,
    )
    record_chart(conn, indicator.id, html_path, image_path, f"eastmoney_public / {note}")
    return {"indicator_id": indicator.id, "factor": factor, "date": date.date().isoformat(), "value": float(value)}


def _positive(frame: pd.DataFrame, column: str) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return values[values > 0]


def refresh(as_of: str) -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    try:
        result = fetch_a_share_snapshot(as_of)
    except Exception as exc:
        report["failed"].append({"source": "eastmoney_public", "error": str(exc)})
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        Path("logs").mkdir(exist_ok=True)
        Path("logs/eastmoney_snapshot_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    conn = ensure_db()
    date = result.as_of
    try:
        metrics: list[tuple[str, float, str]] = []
        amounts = pd.to_numeric(result.frame["amount"], errors="coerce").dropna()
        turnover = _positive(result.frame, "turnover_rate")
        if not amounts.empty:
            metrics.append(("turnover_total", float(amounts.sum()), f"Eastmoney full-A-share amount sum, n={len(amounts)}"))
        if not turnover.empty:
            metrics.append(("turnover_individual_median", float(turnover.median()), f"Eastmoney full-A-share turnover-rate median, n={len(turnover)}"))

        for column, factor in (("pe", "pe"), ("pb", "pb")):
            values = _positive(result.frame, column)
            if values.empty:
                report["skipped"].append({"factor": f"{factor}_distribution", "reason": f"Eastmoney returned no positive finite {column} values"})
                continue
            metrics.extend([
                (f"{factor}_median", float(values.median()), f"Eastmoney positive finite {column}, n={len(values)}"),
                (f"{factor}_p10", float(values.quantile(0.10)), f"Eastmoney positive finite {column}, n={len(values)}, quantile=0.10"),
                (f"{factor}_p90", float(values.quantile(0.90)), f"Eastmoney positive finite {column}, n={len(values)}, quantile=0.90"),
            ])

        for factor, value, note in metrics:
            report["success"].append(_store_scalar(conn, factor, date, value, note))
        report["skipped"].extend([
            {"factor": "turnover_rate", "reason": "Eastmoney provides individual turnover rates but no free-float denominator for a weighted market-wide rate"},
            {"factor": "volume_ratio_20d_median", "reason": "Eastmoney snapshot volume ratio is not documented as a 20-day ratio; not mapped"},
        ])
    finally:
        conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/eastmoney_snapshot_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh latest full-A-share snapshot metrics from Eastmoney")
    parser.add_argument("--as-of", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    print(json.dumps(refresh(args.as_of), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
