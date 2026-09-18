from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.comein_mcp import ComeinMcpClient
from datahub.transforms.ashare import annualized_volatility
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since, upsert_observations


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {
    "IF": ("IF0", "if_close", "if-close", "hs300-close", "if-vol-20d", "if_basis"),
    "IC": ("IC0", "ic_close", "ic-close", "zz500-close", "ic-vol-20d", "ic_basis"),
    "IH": ("IH0", "ih_close", "ih-close", "sz50-close", None, None),
}


def _indicator_by_id(indicator_id: str):
    for indicator in load_indicators():
        if indicator.id == indicator_id:
            return indicator
    raise KeyError(f"Unknown indicator: {indicator_id}")


def _text(result: dict) -> str:
    return "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")


def parse_futures_markdown(text: str) -> pd.DataFrame:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        raise ValueError("Comein futures response contained no Markdown table")
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]
    table = pd.DataFrame(rows, columns=headers)
    if "时间" not in table.columns or "收盘" not in table.columns:
        raise ValueError(f"Comein futures table missing date/close columns: {list(table.columns)}")
    frame = table.rename(columns={"时间": "date", "收盘": "close", "成交量": "volume"})
    if not {"date", "close"}.issubset(frame.columns):
        raise ValueError(f"Comein futures table missing date/close columns: {list(frame.columns)}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if "volume" in frame.columns:
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    return frame.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)


def _store(conn, indicator_id: str, frame: pd.DataFrame, field: str, source_id: str, start: pd.Timestamp, note: str) -> dict:
    indicator = _indicator_by_id(indicator_id)
    rows = [(row.date.to_pydatetime(), float(getattr(row, field)), {"note": note}) for row in frame.itertuples() if pd.notna(getattr(row, field))]
    if not rows:
        return {"indicator_id": indicator_id, "rows": 0}
    upsert_observations(conn, indicator_id, f"{indicator_id}:{field}", source_id, rows, unit=(indicator.chart or {}).get("unit"))
    chart = dict(indicator.chart or {})
    chart["source_label"] = source_id
    html_path, image_path = render_chart(frame[["date", field]], replace(indicator, chart=chart), PROJECT_ROOT / "charts" / indicator_id)
    record_chart(conn, indicator_id, html_path, image_path, f"{source_id} / {note}")
    return {"indicator_id": indicator_id, "rows": len(rows), "date_min": str(frame.date.min().date()), "date_max": str(frame.date.max().date())}


def refresh(limit: int = 500) -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    frames: dict[str, pd.DataFrame] = {}
    try:
        with ComeinMcpClient() as client:
            for variety, (symbol, *_rest) in CONTRACTS.items():
                try:
                    result = client.call_tool("futures_kline", {"market": "internal", "symbol": symbol, "type": 0, "limit": limit})
                    frames[variety] = parse_futures_markdown(_text(result))
                except Exception as exc:
                    report["failed"].append({"variety": variety, "error": str(exc)})
    except Exception as exc:
        report["failed"].append({"tool": "futures_kline", "error": str(exc)})

    if not frames:
        report["skipped"].append({"reason": "Comein futures returned no ingestible tables"})
    else:
        start = min(frame.date.min() for frame in frames.values())
        conn = ensure_db()
        try:
            for variety, frame in frames.items():
                symbol, field, indicator_id, benchmark_id, vol_id, basis_factor = CONTRACTS[variety]
                price_frame = frame.rename(columns={"close": field}).copy()
                report["success"].append(_store(conn, indicator_id, price_frame, field, "comein_mcp", start, f"Comein {symbol} dominant futures daily K-line"))
                if vol_id:
                    vol_field = vol_id.replace("-", "_")
                    vol_frame = price_frame[["date", field]].copy()
                    vol_frame[vol_field] = annualized_volatility(vol_frame[field], window=20)
                    report["success"].append(_store(conn, vol_id, vol_frame[["date", vol_field]], vol_field, "derived", start, f"annualized 20-day volatility from Comein {symbol} daily close"))
                    benchmark = pd.read_sql_query("SELECT obs_time AS date, value FROM observations WHERE indicator_id = ? ORDER BY obs_time", conn, params=[benchmark_id])
                    basis_frame = price_frame[["date", field]].merge(benchmark, on="date", how="inner")
                    basis_frame[basis_factor] = basis_frame[field] - basis_frame["value"]
                    report["success"].append(_store(conn, basis_factor.replace("_", "-"), basis_frame[["date", basis_factor]], basis_factor, "derived", start, f"{symbol} close minus {benchmark_id} close"))
        finally:
            conn.close()
    report["skipped"].extend([
        {"indicator_id": "if-basis-annual", "reason": "Comein futures K-line has no stable expiry-date field"},
        {"indicator_id": "ic-basis-annual", "reason": "Comein futures K-line has no stable expiry-date field"},
    ])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/comein_futures_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(refresh(), ensure_ascii=False, indent=2))
