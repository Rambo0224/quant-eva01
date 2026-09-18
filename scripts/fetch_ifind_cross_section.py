from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.ifind_market import (
    fetch_full_a_share_csv,
)
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"Unknown factor: {factor}")


def _metric_column(frame: pd.DataFrame, keyword: str) -> str:
    candidates = [str(column) for column in frame.columns if keyword in str(column)]
    preferred = [column for column in candidates if not any(token in column for token in ("分位数", "评价", "排名"))]
    if preferred:
        return preferred[0]
    if candidates:
        return candidates[0]
    raise KeyError(f"No column containing {keyword!r}; columns={list(frame.columns)}")


def _store_scalar(conn, factor: str, date: pd.Timestamp, value: float, source: str, note: str) -> dict:
    indicator = _indicator_by_factor(factor)
    field = factor
    row = (date.to_pydatetime(), float(value), {"note": note})
    replace_observations_since(
        conn,
        indicator.id,
        f"{indicator.id}:{field}",
        source,
        [row],
        since=date.to_pydatetime(),
        unit=(indicator.chart or {}).get("unit"),
    )
    plot_frame = pd.DataFrame({"date": [date], field: [float(value)]})
    chart = dict(indicator.chart or {})
    chart["source_label"] = source
    html_path, image_path = render_chart(
        plot_frame,
        replace(indicator, chart=chart),
        PROJECT_ROOT / "charts" / indicator.id,
    )
    record_chart(conn, indicator.id, html_path, image_path, f"{source} / {note}")
    return {"indicator_id": indicator.id, "factor": factor, "date": date.date().isoformat(), "value": float(value)}


def _numeric_metric(frame: pd.DataFrame, keyword: str) -> pd.Series:
    column = _metric_column(frame, keyword)
    return pd.to_numeric(frame[column], errors="coerce")


def refresh(trade_date: str, report_date: str) -> dict:
    trade_day = pd.Timestamp(trade_date)
    report_day = pd.Timestamp(report_date)
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    conn = ensure_db()
    try:
        snapshot: dict[str, pd.DataFrame] = {}
        for field, keyword in (
            ("change", "涨跌幅"),
            ("amount", "成交额"),
            ("turnover", "换手率"),
        ):
            try:
                snapshot[field] = fetch_full_a_share_csv(trade_date, keyword, retries=3).frame
            except Exception as exc:
                report["failed"].append({"field": field, "error": str(exc)})

        if "change" in snapshot:
            changes = _numeric_metric(snapshot["change"], "涨跌幅").dropna()
            up = int((changes > 0).sum())
            down = int((changes < 0).sum())
            if up + down:
                value = up / (up + down)
                for factor in ("advance_ratio",):
                    report["success"].append(_store_scalar(conn, factor, trade_day, value, "ifind_mcp", f"iFinD full-A-share daily breadth: up={up}, down={down}"))
                # The catalog contains the same factor twice under separate indicators.
                duplicate = _indicator_by_factor("advance_ratio")
                duplicate_id = "advance-ratio-2"
                indicator = next(item for item in load_indicators() if item.id == duplicate_id)
                row = (trade_day.to_pydatetime(), float(value), {"note": f"iFinD full-A-share daily breadth: up={up}, down={down}"})
                replace_observations_since(conn, indicator.id, f"{indicator.id}:advance_ratio", "ifind_mcp", [row], trade_day.to_pydatetime(), unit=(indicator.chart or {}).get("unit"))
                chart = dict(indicator.chart or {})
                chart["source_label"] = "ifind_mcp"
                html_path, image_path = render_chart(pd.DataFrame({"date": [trade_day], "advance_ratio": [value]}), replace(indicator, chart=chart), PROJECT_ROOT / "charts" / indicator.id)
                record_chart(conn, indicator.id, html_path, image_path, "ifind_mcp / full-A-share daily breadth")
                report["success"].append({"indicator_id": duplicate_id, "factor": "advance_ratio", "date": trade_day.date().isoformat(), "value": value})

        if "amount" in snapshot:
            amounts = _numeric_metric(snapshot["amount"], "成交额").dropna()
            if not amounts.empty:
                report["success"].append(_store_scalar(conn, "turnover_total", trade_day, amounts.sum(), "ifind_mcp", f"sum of {len(amounts)} full-A-share成交额 rows"))

        if "turnover" in snapshot:
            turnover = _numeric_metric(snapshot["turnover"], "换手率").dropna()
            if not turnover.empty:
                report["success"].append(_store_scalar(conn, "turnover_individual_median", trade_day, turnover.median(), "ifind_mcp", f"median of {len(turnover)} full-A-share individual turnover rates"))
        report["skipped"].append({"indicator_id": "turnover-rate", "reason": "iFinD supplied individual turnover rates, but no free-float-share denominator for a market-wide weighted turnover rate"})

        count = len(snapshot.get("change", next(iter(snapshot.values()), pd.DataFrame())))
        for indicator_id, count_factor in (("limit-up-ratio", "limit-up-count"), ("limit-down-ratio", "limit-down-count")):
            if count <= 0:
                continue
            row = conn.execute("SELECT value FROM observations WHERE indicator_id = ? AND obs_time = ? ORDER BY ingested_at DESC LIMIT 1", [count_factor, trade_day.to_pydatetime()]).fetchone()
            if row and row[0] is not None:
                factor = "limit_up_ratio" if indicator_id == "limit-up-ratio" else "limit_down_ratio"
                report["success"].append(_store_scalar(conn, factor, trade_day, float(row[0]) / count, "derived", f"limit count / {count} full-A-share rows"))

    finally:
        conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/ifind_cross_section_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh verified iFinD A-share cross-sectional indicators")
    parser.add_argument("--trade-date", default="2026-07-16")
    parser.add_argument("--report-date", default="2025-12-31")
    args = parser.parse_args()
    print(json.dumps(refresh(args.trade_date, args.report_date), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
