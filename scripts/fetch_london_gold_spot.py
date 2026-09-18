from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import akshare as ak

from macro_replay.charts import render_chart
from macro_replay.config import find_indicator, load_indicators
from macro_replay.db import ensure_db, record_chart


SOURCE_ID = "akshare_sina_foreign_xau"
ASSET_ID = "london-gold-spot"
SYMBOL = "XAU"
UNIT = "USD/oz"
CLOSE_INDICATOR_ID = "london-gold-spot-close"
RSI_INDICATOR_ID = "london-gold-rsi-14d"
RSI_PERIOD = 14
DEFAULT_START = "2006-08-11"
LOG_PATH = PROJECT_ROOT / "logs" / "london_gold_spot_refresh.json"


def _ensure_market_daily_bars(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_daily_bars (
            source_id TEXT,
            asset_id TEXT,
            symbol TEXT,
            obs_time TIMESTAMP,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            open_interest DOUBLE,
            unit TEXT,
            extra JSON,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _fetch_xau_history() -> pd.DataFrame:
    frame = ak.futures_foreign_hist(symbol=SYMBOL)
    if frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "open_interest"])

    result = frame.rename(columns={"position": "open_interest"}).copy()
    required = ["date", "open", "high", "low", "close"]
    missing = [column for column in required if column not in result.columns]
    if missing:
        raise ValueError(f"AkShare XAU history missing columns: {missing}")

    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume", "open_interest"]:
        if column not in result.columns:
            result[column] = None
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["date", "open", "high", "low", "close"])
    result = result.drop_duplicates("date", keep="last").sort_values("date")
    return result[["date", "open", "high", "low", "close", "volume", "open_interest"]]


def _filter_window(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    result = frame.copy()
    if start:
        result = result[result["date"] >= pd.Timestamp(start)]
    if end:
        result = result[result["date"] <= pd.Timestamp(end)]
    return result.reset_index(drop=True)


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _replace_market_bars(conn, frame: pd.DataFrame) -> int:
    conn.execute(
        "DELETE FROM market_daily_bars WHERE source_id = ? AND asset_id = ?",
        [SOURCE_ID, ASSET_ID],
    )
    if frame.empty:
        return 0
    rows = [
        (
            SOURCE_ID,
            ASSET_ID,
            SYMBOL,
            row.date.to_pydatetime(),
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            float(row.volume) if pd.notna(row.volume) else None,
            float(row.open_interest) if pd.notna(row.open_interest) else None,
            UNIT,
            {"provider": "akshare.futures_foreign_hist", "provider_symbol": SYMBOL},
        )
        for row in frame.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT INTO market_daily_bars
        (source_id, asset_id, symbol, obs_time, open, high, low, close,
         volume, open_interest, unit, extra)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _replace_indicator_series(conn, indicator_id: str, field: str, frame: pd.DataFrame, unit: str) -> int:
    series_id = f"{indicator_id}:{field}"
    conn.execute(
        "DELETE FROM observations WHERE indicator_id = ? AND series_id = ?",
        [indicator_id, series_id],
    )
    if frame.empty or field not in frame.columns:
        return 0
    rows = [
        (
            series_id,
            indicator_id,
            SOURCE_ID,
            row.date.to_pydatetime(),
            float(getattr(row, field)),
            unit,
            {"asset_id": ASSET_ID, "provider_symbol": SYMBOL},
        )
        for row in frame[["date", field]].dropna().itertuples(index=False)
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO observations
        (series_id, indicator_id, source_id, obs_time, value, unit, extra)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _render(indicator_id: str, field: str, frame: pd.DataFrame, end: str | None = None) -> dict[str, Any]:
    indicator = find_indicator(load_indicators(), indicator_id)
    chart = dict(indicator.chart or {})
    chart["source_label"] = SOURCE_ID
    applied = replace(indicator, chart=chart)
    args = indicator.arguments or {}
    plot_frame = _filter_window(
        frame,
        args.get("start_date"),
        args.get("end_date") or end,
    )
    html_path, image_path = render_chart(
        plot_frame[["date", field]].dropna().rename(columns={field: chart.get("y_field", field)}),
        applied,
        PROJECT_ROOT / "charts" / indicator_id,
    )
    return {
        "indicator_id": indicator_id,
        "html_path": str(html_path),
        "image_path": str(image_path),
    }


def refresh(start: str | None = DEFAULT_START, end: str | None = None) -> dict[str, Any]:
    end = end or date.today().isoformat()
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_id": SOURCE_ID,
        "asset_id": ASSET_ID,
        "provider": "akshare.futures_foreign_hist",
        "provider_symbol": SYMBOL,
        "success": [],
        "failed": [],
    }
    try:
        fetched = _fetch_xau_history()
        full = _filter_window(fetched, start, end)
        if full.empty:
            raise ValueError(f"No XAU rows returned for window {start} to {end}")
        full = full.copy()
        full["london_gold_spot_close"] = full["close"]
        full["london_gold_rsi_14d"] = _rsi(full["close"])

        conn = ensure_db()
        try:
            _ensure_market_daily_bars(conn)
            previous=conn.execute('SELECT obs_time AS date,open,high,low,close,volume,open_interest FROM market_daily_bars WHERE source_id=? AND asset_id=?',[SOURCE_ID,ASSET_ID]).df()
            # The provider may return a rolling window. Merge raw prices before
            # recomputing RSI so older valid history is never truncated.
            if not previous.empty:
                full=pd.concat([previous,full],ignore_index=True).drop_duplicates('date',keep='last').sort_values('date').reset_index(drop=True)
                full['london_gold_spot_close']=full['close']
                full['london_gold_rsi_14d']=_rsi(full['close'])
            conn.execute('BEGIN')
            bar_rows = _replace_market_bars(conn, full)
            close_rows = _replace_indicator_series(
                conn,
                CLOSE_INDICATOR_ID,
                "london_gold_spot_close",
                full,
                UNIT,
            )
            rsi_rows = _replace_indicator_series(
                conn,
                RSI_INDICATOR_ID,
                "london_gold_rsi_14d",
                full,
                "index",
            )
            conn.execute('COMMIT')
            close_chart = _render(CLOSE_INDICATOR_ID, "london_gold_spot_close", full, end=end)
            rsi_chart = _render(RSI_INDICATOR_ID, "london_gold_rsi_14d", full, end=end)
            record_chart(
                conn,
                CLOSE_INDICATOR_ID,
                Path(close_chart["html_path"]),
                Path(close_chart["image_path"]),
                SOURCE_ID,
            )
            record_chart(
                conn,
                RSI_INDICATOR_ID,
                Path(rsi_chart["html_path"]),
                Path(rsi_chart["image_path"]),
                SOURCE_ID,
            )
        finally:
            conn.close()

        report["success"].append(
            {
                "rows": len(full),
                "market_bar_rows": bar_rows,
                "close_observation_rows": close_rows,
                "rsi_observation_rows": rsi_rows,
                "date_min": str(full["date"].min().date()),
                "date_max": str(full["date"].max().date()),
                "last_close": float(full["close"].iloc[-1]),
                "last_rsi_14d": float(full["london_gold_rsi_14d"].dropna().iloc[-1]),
                "charts": [close_chart, rsi_chart],
            }
        )
    except Exception as exc:
        report["failed"].append({"error": str(exc)})
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        LOG_PATH.parent.mkdir(exist_ok=True)
        LOG_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch London spot gold XAU daily bars and compute RSI(14).")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    print(json.dumps(refresh(args.start, args.end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
