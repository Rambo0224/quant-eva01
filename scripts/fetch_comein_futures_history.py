from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.comein_mcp import ComeinMcpClient
from macro_replay.db import ensure_db
from scripts.fetch_comein_futures import CONTRACTS, _store, parse_futures_markdown


def _text(result: dict) -> str:
    return "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")


def monthly_contract_codes(variety: str, start: str, end: str) -> list[str]:
    months = pd.period_range(pd.Timestamp(start).to_period("M"), pd.Timestamp(end).to_period("M"), freq="M")
    return [f"{variety}{period.strftime('%y%m')}" for period in months]


def select_dominant_contract(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["date", "close"])
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    if "volume" in result.columns:
        result["volume"] = pd.to_numeric(result["volume"], errors="coerce").fillna(-1)
    else:
        result["volume"] = -1
    result = result.dropna(subset=["date", "close"])
    result = result[(result["date"] >= pd.Timestamp(start)) & (result["date"] <= pd.Timestamp(end))]
    result = result.sort_values(["date", "volume", "symbol"], ascending=[True, False, True])
    return result.drop_duplicates("date", keep="first").sort_values("date").reset_index(drop=True)


def refresh(start: str, end: str, limit: int = 500) -> dict:
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    selected_frames: dict[str, pd.DataFrame] = {}
    try:
        with ComeinMcpClient(timeout=60) as client:
            for variety in CONTRACTS:
                frames: list[pd.DataFrame] = []
                for symbol in monthly_contract_codes(variety, start, end):
                    try:
                        result = client.call_tool(
                            "futures_kline",
                            {"market": "internal", "symbol": symbol, "type": 0, "limit": limit},
                        )
                        frame = parse_futures_markdown(_text(result))
                        if not frame.empty:
                            frame["symbol"] = symbol
                            frames.append(frame)
                    except Exception as exc:
                        report["failed"].append({"variety": variety, "symbol": symbol, "error": str(exc)})
                if frames:
                    selected = select_dominant_contract(pd.concat(frames, ignore_index=True), start, end)
                    if not selected.empty and (selected["date"].max() - selected["date"].min()).days >= 365:
                        selected_frames[variety] = selected
                    else:
                        report["failed"].append({"variety": variety, "error": "stitched history is shorter than one year"})
    except Exception as exc:
        report["failed"].append({"tool": "futures_kline", "error": str(exc)})

    if selected_frames:
        conn = ensure_db()
        try:
            for variety, frame in selected_frames.items():
                _, field, indicator_id, benchmark_id, vol_id, basis_factor = CONTRACTS[variety]
                price_frame = frame[["date", "close"]].rename(columns={"close": field})
                report["success"].append(_store(conn, indicator_id, price_frame, field, "comein_mcp", pd.Timestamp(start), f"Comein monthly {variety} contracts selected by daily volume"))
                if vol_id:
                    vol_field = vol_id.replace("-", "_")
                    vol_frame = price_frame[["date", field]].copy()
                    from datahub.transforms.ashare import annualized_volatility

                    vol_frame[vol_field] = annualized_volatility(vol_frame[field], window=20)
                    report["success"].append(_store(conn, vol_id, vol_frame[["date", vol_field]], vol_field, "derived", pd.Timestamp(start), f"annualized 20-day volatility from stitched Comein {variety} close"))
                    benchmark = pd.read_sql_query(
                        "SELECT obs_time AS date, value FROM observations WHERE indicator_id = ? ORDER BY obs_time",
                        conn,
                        params=[benchmark_id],
                    )
                    basis_frame = price_frame.merge(benchmark, on="date", how="inner")
                    basis_frame[basis_factor] = basis_frame[field] - basis_frame["value"]
                    report["success"].append(_store(conn, basis_factor.replace("_", "-"), basis_frame[["date", basis_factor]], basis_factor, "derived", pd.Timestamp(start), f"stitched {variety} close minus {benchmark_id} close"))
        finally:
            conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/comein_futures_history_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill one year of Comein index-futures history")
    parser.add_argument("--start", default=(pd.Timestamp.today() - pd.DateOffset(years=1)).date().isoformat())
    parser.add_argument("--end", default=pd.Timestamp.today().date().isoformat())
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(refresh(args.start, args.end, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
