from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.comein_mcp import ComeinMcpClient
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SORT = [
    {"field": "turnoverValue", "order": "desc"},
    {"field": "turnoverRate", "order": "desc"},
    {"field": "changePctWeek", "order": "desc"},
    {"field": "changePctMonth", "order": "desc"},
    {"field": "changePctThreeMonth", "order": "desc"},
    {"field": "changePctSixMonth", "order": "desc"},
    {"field": "changePctYtd", "order": "desc"},
]
NUMERIC = [
    "marketCap", "latestPrice", "turnoverValue", "turnoverRate",
    "changePctWeek", "changePctMonth", "changePctThreeMonth",
    "changePctSixMonth", "changePctYtd",
]


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"Unknown factor: {factor}")


def _store(conn, factor: str, date: pd.Timestamp, value: float, note: str) -> dict:
    indicator = _indicator_by_factor(factor)
    replace_observations_since(
        conn,
        indicator.id,
        f"{indicator.id}:{factor}",
        "comein_mcp",
        [(date.to_pydatetime(), float(value), {"note": note})],
        since=date.to_pydatetime(),
        unit=(indicator.chart or {}).get("unit"),
    )
    chart = dict(indicator.chart or {})
    chart["source_label"] = "comein_mcp"
    html_path, image_path = render_chart(
        pd.DataFrame({"date": [date], factor: [float(value)]}),
        replace(indicator, chart=chart),
        PROJECT_ROOT / "charts" / indicator.id,
    )
    record_chart(conn, indicator.id, html_path, image_path, f"comein_mcp / {note}")
    return {"indicator_id": indicator.id, "factor": factor, "date": date.date().isoformat(), "value": float(value)}


def _page_data(result: dict) -> dict:
    for content in result.get("content", []):
        if content.get("type") == "text":
            outer = json.loads(content["text"])
            return outer["data"]["data"]
    raise ValueError("Comein screener response has no text data")


def fetch_snapshot(client: ComeinMcpClient, page_size: int) -> pd.DataFrame:
    common = {
        "size": page_size,
        "sort": SORT,
        "enumConditions": [{"key": "marketType", "values": ["sh", "sz", "bj"]}],
        "numberConditions": [],
    }
    first = _page_data(client.call_tool("screenerStock", {**common, "page": 1}))
    page_info = first["pageInfo"]
    total = int(page_info["total"])
    rows = list(first["stockList"])
    for page in range(2, int(page_info["totalPages"]) + 1):
        payload = _page_data(client.call_tool("screenerStock", {**common, "page": page}))
        rows.extend(payload["stockList"])
    frame = pd.DataFrame(rows)
    frame["stockCode"] = frame["stockCode"].astype("string")
    for column in NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.drop_duplicates("stockCode").reset_index(drop=True)
    if len(frame) < total * 0.95:
        raise ValueError(f"Comein screener snapshot incomplete: {len(frame)} rows for total {total}")
    return frame


def _positive(frame: pd.DataFrame, column: str) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return values[values > 0]


def refresh(as_of: str, page_size: int = 100) -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    try:
        with ComeinMcpClient() as client:
            frame = fetch_snapshot(client, page_size)
    except Exception as exc:
        report["failed"].append({"tool": "screenerStock", "error": str(exc)})
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        Path("logs").mkdir(exist_ok=True)
        Path("logs/comein_screener_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    date = pd.Timestamp(as_of)
    metrics: list[tuple[str, float, str]] = []
    amounts = pd.to_numeric(frame["turnoverValue"], errors="coerce").dropna()
    turnover = _positive(frame, "turnoverRate")
    if not amounts.empty:
        metrics.append(("turnover_total", float(amounts.sum()), f"Comein full-A-share screener turnoverValue sum, n={len(amounts)}"))
    if not turnover.empty:
        metrics.append(("turnover_individual_median", float(turnover.median()), f"Comein full-A-share screener turnoverRate median, n={len(turnover)}"))

    for source_column, factor, label in (
        ("changePctWeek", "reversal_5d_median", "1-week return as approximately 5 trading days"),
        ("changePctMonth", "momentum_20d_median", "1-month return as approximately 20 trading days"),
        ("changePctThreeMonth", "momentum_60d_median", "3-month return as approximately 60 trading days"),
    ):
        values = pd.to_numeric(frame[source_column], errors="coerce").dropna()
        if not values.empty:
            metrics.append((factor, float(values.median()), f"Comein full-A-share {source_column} median; {label}"))
    month_change = pd.to_numeric(frame["changePctMonth"], errors="coerce").dropna()
    if not month_change.empty:
        metrics.append(("advance_ratio_20d", float((month_change > 0).mean()), "Comein full-A-share positive 1-month return ratio; approximately 20 trading days"))

    conn = ensure_db()
    try:
        for factor, value, note in metrics:
            report["success"].append(_store(conn, factor, date, value, note))
    finally:
        conn.close()
    report["skipped"].extend([
        {"factor": "turnover_rate", "reason": "No free-float denominator for a weighted market-wide turnover rate"},
        {"factor": "volume_ratio_20d_median", "reason": "Screener does not return a documented 20-day volume ratio"},
        {"factor": "high_60d_ratio", "reason": "Screener does not return rolling high flags"},
        {"factor": "above_ma20_ratio", "reason": "Screener does not return moving-average flags"},
    ])
    report["rows"] = len(frame)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/comein_screener_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Refresh full-A cross-sectional metrics from Comein screener")
    parser.add_argument("--as-of", default=pd.Timestamp.now().strftime("%Y-%m-%d"))
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(refresh(args.as_of, args.page_size), ensure_ascii=False, indent=2))
