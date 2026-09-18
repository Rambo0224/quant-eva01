from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from datahub.transforms.ashare import annualized_volatility, rolling_rsi
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import DB_PATH, ensure_db, record_chart, replace_observations_since


DERIVATIONS = {
    "hs300_close": [
        {
            "factor": "hs300_vol_20d",
            "calculation": "pct_change().rolling(20).std()*sqrt(252)",
            "source_label": "DERIVED / annualized_volatility_20d",
        }
    ],
    "sz50_close": [
        {
            "factor": "sz50_vol_20d",
            "calculation": "pct_change().rolling(20).std()*sqrt(252)",
            "source_label": "DERIVED / annualized_volatility_20d",
        }
    ],
    "cyb50_close": [
        {
            "factor": "cyb50_vol_20d",
            "calculation": "pct_change().rolling(20).std()*sqrt(252)",
            "source_label": "DERIVED / annualized_volatility_20d",
        }
    ],
    "kc50_close": [
        {
            "factor": "kc50_vol_20d",
            "calculation": "pct_change().rolling(20).std()*sqrt(252)",
            "source_label": "DERIVED / annualized_volatility_20d",
        },
        {
            "factor": "kc50_rsi_14d",
            "calculation": "rolling_rsi(close, 14)",
            "source_label": "DERIVED / rolling_rsi_14d",
        },
    ],
}


def _calculate_derived(close: pd.Series, calculation: str) -> pd.Series:
    if calculation == "pct_change().rolling(20).std()*sqrt(252)":
        return annualized_volatility(close, window=20)
    if calculation == "rolling_rsi(close, 14)":
        return rolling_rsi(close, window=14)
    raise ValueError(f"unsupported index derivation calculation: {calculation}")


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"No catalog indicator found for {factor}")


def compute() -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": []}
    source_conn = duckdb.connect(str(DB_PATH), read_only=True)
    base_frames: dict[str, pd.DataFrame] = {}
    try:
        for base_factor, derivations in DERIVATIONS.items():
            try:
                base_indicator = _indicator_by_factor(base_factor)
                rows = source_conn.execute(
                    """
                    SELECT obs_time, value
                    FROM observations
                    WHERE indicator_id = ? AND series_id = ?
                    QUALIFY row_number() OVER (PARTITION BY obs_time ORDER BY ingested_at DESC,source_id)=1
                    ORDER BY obs_time
                    """,
                    [base_indicator.id, f"{base_indicator.id}:{base_factor}"],
                ).fetchall()
                if not rows:
                    raise ValueError(f"no base observations for {base_factor}")
                frame = pd.DataFrame(rows, columns=["date", base_factor])
                frame["date"] = pd.to_datetime(frame["date"])
                for derivation in derivations:
                    derived_factor = derivation["factor"]
                    derived_frame = frame[["date", base_factor]].copy()
                    derived_frame[derived_factor] = _calculate_derived(derived_frame[base_factor], derivation["calculation"])
                    derived_frame = derived_frame[["date", derived_factor]].dropna()
                    base_frames[derived_factor] = {
                        "frame": derived_frame,
                        "calculation": derivation["calculation"],
                        "source_label": derivation["source_label"],
                    }
            except Exception as exc:
                failed_factors = [item["factor"] for item in derivations]
                report["failed"].append({"factor": ",".join(failed_factors), "error": str(exc)})
    finally:
        source_conn.close()

    target_conn = ensure_db()
    try:
        for derived_factor, payload in base_frames.items():
            try:
                frame = payload["frame"]
                calculation = payload["calculation"]
                source_label = payload["source_label"]
                derived_indicator = _indicator_by_factor(derived_factor)
                stored = [
                    (row["date"].to_pydatetime(), float(row[derived_factor]), {"calculation": calculation})
                    for _, row in frame.iterrows()
                ]
                replace_observations_since(
                    target_conn,
                    derived_indicator.id,
                    f"{derived_indicator.id}:{derived_factor}",
                    "derived",
                    stored,
                    since=frame["date"].min().to_pydatetime(),
                    unit=(derived_indicator.chart or {}).get("unit"),
                )
                chart = dict(derived_indicator.chart or {})
                chart["source_label"] = "derived"
                chart_indicator = replace(derived_indicator, chart=chart)
                html_path, image_path = render_chart(frame, chart_indicator, Path("charts") / derived_indicator.id)
                record_chart(target_conn, derived_indicator.id, html_path, image_path, source_label)
                report["success"].append({"factor": derived_factor, "rows": len(stored), "date_min": str(frame["date"].min().date()), "date_max": str(frame["date"].max().date())})
            except Exception as exc:
                report["failed"].append({"factor": derived_factor, "error": str(exc)})
    finally:
        target_conn.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/index_derived_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(compute(), ensure_ascii=False, indent=2))
