from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.comein_mcp import ComeinMcpClient
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since
from macro_replay.source_labels import display_source_label


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEXES = {
    "000300": {"close": "hs300_close", "pe": "hs300_pe", "pb": "hs300_pb"},
    "000016": {"close": "sz50_close", "pe": "sz50_pe", "pb": "sz50_pb"},
    "399673": {"close": "cyb50_close", "pe": "cyb50_pe", "pb": "cyb50_pb"},
    "000688": {"close": "kc50_close", "pe": "kc50_pe", "pb": "kc50_pb"},
    "000905": {"close": "zz500_close"},
}


def parse_comein_json(result: dict) -> list[dict]:
    """Extract the structured data array from a Comein MCP tool result."""
    for content in result.get("content", []):
        if content.get("type") != "text":
            continue
        payload = json.loads(content["text"])
        data = payload.get("data")
        if isinstance(data, list):
            return data
    raise ValueError("Comein response does not contain a structured data array")


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"No catalog indicator found for {factor}")


def _normalize_series(rows: list[dict], value_field: str, start: str, end: str) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    required = {"trading_day", value_field}
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame(columns=["date", value_field])
    frame = frame[["trading_day", value_field]].rename(columns={"trading_day": "date"})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame[value_field] = pd.to_numeric(frame[value_field], errors="coerce")
    frame = frame.dropna(subset=["date", value_field])
    frame = frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _store(conn, factor: str, frame: pd.DataFrame, start: str, index_code: str, mode: str) -> dict:
    indicator = _indicator_by_factor(factor)
    rows = [
        (
            row["date"].to_pydatetime(),
            float(row[factor]),
            {"index_code": index_code, "mode": mode},
        )
        for _, row in frame[["date", factor]].dropna().iterrows()
    ]
    replace_observations_since(
        conn,
        indicator.id,
        f"{indicator.id}:{factor}",
        "comein_mcp",
        rows,
        since=pd.Timestamp(start).to_pydatetime(),
        unit=(indicator.chart or {}).get("unit"),
    )
    chart = dict(indicator.chart or {})
    chart["source_label"] = "comein_mcp"
    html_path, image_path = render_chart(
        frame[["date", factor]],
        replace(indicator, chart=chart),
        PROJECT_ROOT / "charts" / indicator.id,
    )
    record_chart(conn, indicator.id, html_path, image_path, f"{display_source_label('comein_mcp')} / {mode} / {index_code}")
    return {
        "indicator_id": indicator.id,
        "factor": factor,
        "rows": len(rows),
        "date_min": str(frame["date"].min().date()),
        "date_max": str(frame["date"].max().date()),
    }


def refresh(start: str, end: str) -> dict:
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": []}
    conn = ensure_db()
    try:
        with ComeinMcpClient(timeout=60) as client:
            for index_code, factor_map in INDEXES.items():
                try:
                    kline_rows = parse_comein_json(
                        client.call_tool(
                            "searchIndexQuotation",
                            {
                                "mode": "kline",
                                "queries": [index_code],
                                "start_date": start,
                                "end_date": end,
                                "period_type": "D",
                                "limit": 1000,
                            },
                        )
                    )
                    close_factor = factor_map["close"]
                    close = _normalize_series(kline_rows, "close", start, end).rename(columns={"close": close_factor})
                    if close.empty or (close["date"].max() - close["date"].min()).days < 365:
                        raise ValueError(f"{index_code} close history is shorter than one year")
                    report["success"].append(_store(conn, close_factor, close, start, index_code, "kline-D"))
                except Exception as exc:
                    report["failed"].append({"index_code": index_code, "mode": "kline-D", "error": str(exc)})
                    continue

                if not {"pe", "pb"}.issubset(factor_map):
                    continue
                try:
                    # The vendor valuation series is sampled more sparsely than K-lines;
                    # request a buffer before the target window so the returned span still
                    # covers a full year after source-side sampling.
                    valuation_start = (pd.Timestamp(start) - pd.Timedelta(days=90)).date().isoformat()
                    valuation_rows = parse_comein_json(
                        client.call_tool(
                            "searchIndexQuotation",
                            {
                                "mode": "timeseries",
                                "queries": [index_code],
                                "start_date": valuation_start,
                                "end_date": end,
                                "include_fields": ["valuation"],
                                "limit": 1000,
                            },
                        )
                    )
                    for field in ("pe", "pb"):
                        factor = factor_map[field]
                        frame = _normalize_series(valuation_rows, field, valuation_start, end).rename(columns={field: factor})
                        if frame.empty or (frame["date"].max() - frame["date"].min()).days < 365:
                            raise ValueError(f"{index_code} {field} history is shorter than one year")
                        report["success"].append(_store(conn, factor, frame, valuation_start, index_code, "timeseries-valuation"))
                except Exception as exc:
                    report["failed"].append({"index_code": index_code, "mode": "timeseries-valuation", "error": str(exc)})
    except Exception as exc:
        report["failed"].append({"error": str(exc)})
    finally:
        conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/comein_index_history_refresh.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill one year of Comein index history")
    parser.add_argument("--start", default=(pd.Timestamp.today() - pd.DateOffset(years=1)).date().isoformat())
    parser.add_argument("--end", default=pd.Timestamp.today().date().isoformat())
    args = parser.parse_args()
    print(json.dumps(refresh(args.start, args.end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
