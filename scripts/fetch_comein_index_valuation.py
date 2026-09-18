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
INDEX_MAP = {
    "沪深300": ("hs300_pe", "hs300_pb"),
    "上证50": ("sz50_pe", "sz50_pb"),
    "创业板50": ("cyb50_pe", "cyb50_pb"),
    "科创50": ("kc50_pe", "kc50_pb"),
}


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


def _response_data(result: dict) -> list[dict]:
    for content in result.get("content", []):
        if content.get("type") == "text":
            payload = json.loads(content["text"])
            return payload.get("data", [])
    raise ValueError("Comein index response has no text data")


def refresh() -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    try:
        with ComeinMcpClient() as client:
            result = client.call_tool(
                "searchIndexQuotation",
                {"mode": "snapshot", "queries": list(INDEX_MAP), "include_fields": ["basic", "quote", "valuation"]},
            )
        data = _response_data(result)
    except Exception as exc:
        report["failed"].append({"tool": "searchIndexQuotation", "error": str(exc)})
        data = []

    conn = ensure_db()
    try:
        for item in data:
            query = item.get("query")
            factors = INDEX_MAP.get(query)
            valuation = item.get("valuation", {})
            if not factors or not valuation.get("latest_val_day"):
                report["skipped"].append({"query": query, "reason": "missing valuation date or unmapped index"})
                continue
            date = pd.Timestamp(valuation["latest_val_day"])
            for field, factor in (("pe", factors[0]), ("pb", factors[1])):
                value = pd.to_numeric(pd.Series([valuation.get(field)]), errors="coerce").iat[0]
                if pd.isna(value):
                    report["skipped"].append({"factor": factor, "reason": f"missing {field}"})
                    continue
                percentile = valuation.get(f"{field}_pct_3y")
                note = f"Comein index valuation snapshot: {query}, {field}, 3y percentile={percentile}"
                report["success"].append(_store(conn, factor, date, float(value), note))
    finally:
        conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/comein_index_valuation_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(refresh(), ensure_ascii=False, indent=2))
