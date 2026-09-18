from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import replace

import pandas as pd

from datahub.transforms.ashare import cross_section_statistics
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EQUITY_BAR_START_DATE = "2021-01-01"
BAOSTOCK_SOURCE_ID = "baostock_history_k_data"
AKSHARE_SOURCE_ID = "akshare_stock_zh_a_hist_qfq"
DERIVED_SOURCE_ID = "baostock_derived"
FACTORS = {
    "momentum_20d_median",
    "momentum_60d_median",
    "reversal_5d_median",
    "turnover_individual_median",
    "volume_ratio_20d_median",
    "bias_20d_median",
    "atr_14d_median",
    "rsi_14d_median",
    "advance_ratio_20d",
    "high_60d_ratio",
    "high_120d_ratio",
    "high_250d_ratio",
    "above_ma20_ratio",
    "above_ma60_ratio",
    "above_ma120_ratio",
}
SOURCE_IDS = (BAOSTOCK_SOURCE_ID, AKSHARE_SOURCE_ID, "openbb_yfinance_batch", "openbb_yfinance")


def _indicator_map():
    return {
        indicator.metadata.get("factor_name"): indicator
        for indicator in load_indicators()
        if indicator.metadata.get("factor_name") in FACTORS
    }


def _load_bars(conn) -> pd.DataFrame:
    placeholders = ", ".join("?" for _ in SOURCE_IDS)
    return conn.execute(
        f"""
        SELECT source_id, symbol AS code, obs_time AS date, open, high, low, close, volume,
               amount, turnover_rate
        FROM equity_daily_bars
        WHERE source_id IN ({placeholders})
          AND obs_time >= ?
        ORDER BY symbol, obs_time, source_id
        """,
        [*SOURCE_IDS, EQUITY_BAR_START_DATE],
    ).fetchdf()


def _latest_bar_date(conn):
    placeholders = ", ".join("?" for _ in SOURCE_IDS)
    row = conn.execute(
        f"""
        SELECT max(obs_time)
        FROM equity_daily_bars
        WHERE source_id IN ({placeholders})
        """,
        [*SOURCE_IDS],
    ).fetchone()
    return pd.Timestamp(row[0]).normalize() if row and row[0] is not None else None


def _latest_factor_date(conn, indicator_ids: list[str]):
    if not indicator_ids:
        return None
    placeholders = ", ".join("?" for _ in indicator_ids)
    row = conn.execute(
        f"""
        SELECT count(*), min(latest_date)
        FROM (
            SELECT indicator_id, max(obs_time) AS latest_date
            FROM observations
            WHERE indicator_id IN ({placeholders})
            GROUP BY indicator_id
        )
        """,
        indicator_ids,
    ).fetchone()
    if not row or int(row[0] or 0) < len(indicator_ids) or row[1] is None:
        return None
    return pd.Timestamp(row[1]).normalize()


def refresh() -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "source_id": DERIVED_SOURCE_ID, "success": [], "failed": [], "skipped": []}
    conn = ensure_db()
    try:
        indicators = _indicator_map()
        indicator_ids = [indicator.id for indicator in indicators.values()]
        latest_bar_date = _latest_bar_date(conn)
        latest_factor_date = _latest_factor_date(conn, indicator_ids)
        if latest_bar_date is not None and latest_factor_date is not None and latest_factor_date >= latest_bar_date:
            report["skipped"].append({
                "factor": "all",
                "reason": "technical factors are already up to date with equity_daily_bars",
                "latest_bar_date": latest_bar_date.date().isoformat(),
                "latest_factor_date": latest_factor_date.date().isoformat(),
            })
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            Path("logs").mkdir(exist_ok=True)
            Path("logs/ashare_technical_factors_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return report
        bars = _load_bars(conn)
        if bars.empty:
            report["failed"].append({"error": "no AkShare equity bars in equity_daily_bars"})
            return report
        bars["code"] = bars["code"].astype(str).str.split(".").str[0].str.zfill(6)
        bars["source_priority"] = bars["source_id"].map({BAOSTOCK_SOURCE_ID: 0, AKSHARE_SOURCE_ID: 1, "openbb_yfinance_batch": 2, "openbb_yfinance": 3}).fillna(9)
        bars = bars.sort_values(["code", "date", "source_priority"]).drop_duplicates(["code", "date"], keep="first")
        bars = bars.drop(columns=["source_id", "source_priority"])
        stats = cross_section_statistics(bars)
        if indicator_ids:
            placeholders = ", ".join("?" for _ in indicator_ids)
            conn.execute(f"DELETE FROM observations WHERE indicator_id IN ({placeholders})", indicator_ids)
        missing = sorted(FACTORS.difference(stats.columns).difference({"turnover_individual_median"}))
        if missing:
            report["skipped"].extend({"factor": factor, "reason": "raw bars do not yet provide enough history or required fields"} for factor in missing)
        first_date = pd.Timestamp(stats["date"].min()).to_pydatetime()
        for factor in sorted(FACTORS.intersection(stats.columns)):
            indicator = indicators.get(factor)
            if indicator is None:
                report["skipped"].append({"factor": factor, "reason": "factor is not present in indicator catalog"})
                continue
            frame = stats[["date", factor]].dropna().copy()
            if frame.empty:
                report["skipped"].append({"factor": factor, "reason": "factor has no valid warm-up observations"})
                continue
            rows = [
                (row.date.to_pydatetime(), float(getattr(row, factor)), {"source": DERIVED_SOURCE_ID, "calculation": "cross_section_statistics"})
                for row in frame.itertuples(index=False)
            ]
            series_id = f"{indicator.id}:{DERIVED_SOURCE_ID}"
            replace_observations_since(
                conn,
                indicator.id,
                series_id,
                DERIVED_SOURCE_ID,
                rows,
                since=first_date,
                unit=(indicator.chart or {}).get("unit"),
            )
            chart = dict(indicator.chart or {})
            chart["source_label"] = "BaoStock daily OHLCV preferred + derived"
            html_path, image_path = render_chart(
                frame,
                replace(indicator, chart=chart),
                PROJECT_ROOT / "charts" / indicator.id,
            )
            record_chart(conn, indicator.id, html_path, image_path, "BaoStock daily-all OHLCV preferred; AkShare/OpenBB fallback; calculated cross-section")
            report["success"].append({
                "factor": factor,
                "indicator_id": indicator.id,
                "rows": len(frame),
                "date_min": frame["date"].min().date().isoformat(),
                "date_max": frame["date"].max().date().isoformat(),
            })
        report["bars"] = len(bars)
        report["symbols"] = int(bars["code"].nunique())
        report["factor_rows"] = len(stats)
    except Exception as exc:
        report["failed"].append({"error": str(exc)})
        raise
    finally:
        conn.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/ashare_technical_factors_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(refresh(), ensure_ascii=False, indent=2))
