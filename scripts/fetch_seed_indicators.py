from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path

import pandas as pd

from datahub.adapters.akshare_public import (
    AkshareSourceUnavailable,
    fetch_index_daily,
    fetch_index_valuation,
    fetch_sse_margin_history,
)
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since
from macro_replay.source_labels import display_source_label


INDEXES = {
    "hs300_close": ("sh000300", "hs300-close"),
    "sz50_close": ("sh000016", "sz50-close"),
    "cyb50_close": ("sz399673", "cyb50-close"),
    "kc50_close": ("sh000688", "kc50-close"),
    "zz500_close": ("sh000905", "zz500-close"),
}


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"No catalog indicator found for {factor}")


def _store_series(conn, indicator, factor: str, frame: pd.DataFrame, source_id: str, endpoint: str, note: str, start_date: str) -> dict:
    rows = []
    for _, row in frame[["date", factor]].dropna().iterrows():
        rows.append(
            (
                pd.Timestamp(row["date"]).to_pydatetime(),
                float(row[factor]),
                {"endpoint": endpoint, "note": note},
            )
        )
    replace_observations_since(
        conn,
        indicator.id,
        f"{indicator.id}:{factor}",
        source_id,
        rows,
        since=pd.Timestamp(start_date).to_pydatetime(),
        unit=(indicator.chart or {}).get("unit"),
    )
    plot_df = frame[["date", factor]].copy()
    chart_config = dict(indicator.chart or {})
    chart_config["source_label"] = source_id
    render_indicator = replace(indicator, chart=chart_config)
    html_path, image_path = render_chart(plot_df, render_indicator, Path("charts") / indicator.id)
    record_chart(conn, indicator.id, html_path, image_path, f"{display_source_label(source_id)} / {endpoint}")
    return {
        "indicator_id": indicator.id,
        "factor": factor,
        "rows": len(rows),
        "date_min": str(plot_df["date"].min().date()) if not plot_df.empty else None,
        "date_max": str(plot_df["date"].max().date()) if not plot_df.empty else None,
        "html_path": str(html_path),
        "image_path": str(image_path),
    }


def refresh(start_date: str, end_date: str | None = None) -> dict:
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "skipped": [], "failed": []}
    conn = ensure_db()
    try:
        for factor, (symbol, indicator_id) in INDEXES.items():
            try:
                result = fetch_index_daily(symbol, factor, start_date=start_date, end_date=end_date)
                indicator = _indicator_by_factor(factor)
                report["success"].append(_store_series(conn, indicator, factor, result.frame, result.source_id, result.endpoint, result.note, start_date))
            except Exception as exc:
                report["failed"].append({"factor": factor, "symbol": symbol, "error": str(exc)})

        try:
            margin = fetch_sse_margin_history(start_date=start_date, end_date=end_date)
            report["skipped"].append({
                "factor": "margin_balance/margin_purchase/short_balance",
                "reason": "AkShare 返回上交所口径，不能直接冒充全市场合计；已完成抓取验证但暂不绘图",
                "rows": len(margin.frame),
                "endpoint": margin.endpoint,
            })
        except Exception as exc:
            report["failed"].append({"factor": "sse_margin_history", "error": str(exc)})

        try:
            valuation = fetch_index_valuation("000300", start_date=start_date, end_date=end_date)
            report["skipped"].append({
                "factor": "hs300_pe",
                "reason": "AkShare 返回市盈率1/市盈率2两个口径，待确认后再映射",
                "rows": len(valuation.frame),
                "endpoint": valuation.endpoint,
            })
        except Exception as exc:
            report["failed"].append({"factor": "hs300_valuation", "error": str(exc)})
    finally:
        conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/seed_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a conservative first batch of A-share indicators")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    print(json.dumps(refresh(args.start, args.end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
