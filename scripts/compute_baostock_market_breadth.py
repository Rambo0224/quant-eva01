from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from macro_replay.charts import render_chart
from macro_replay.config import Indicator, load_indicators
from macro_replay.db import ensure_db, record_chart, replace_observations_since


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BAOSTOCK_SOURCE_ID = "baostock_history_k_data"
DERIVED_SOURCE_ID = "baostock_market_breadth"
TARGET_FACTORS = {
    "turnover_rate",
    "advance_decline_line",
    "advance_ratio",
    "limit_up_count",
    "limit_down_count",
    "limit_up_ratio",
    "limit_down_ratio",
    "explosive_ratio",
}
INTERNAL_FACTORS = {
    "advance_decline_net": {
        "indicator_id": "advance_decline_net",
        "series_id": "advance_decline_net:baostock_market_breadth",
        "unit": "count",
        "calculation": "up_count - down_count",
    },
}


def _indicators_by_factor() -> dict[str, list[Indicator]]:
    result: dict[str, list[Indicator]] = {}
    for indicator in load_indicators():
        factor = indicator.metadata.get("factor_name")
        if factor in TARGET_FACTORS:
            result.setdefault(factor, []).append(indicator)
    return result


def _load_bars(conn) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT
            symbol,
            obs_time AS date,
            high,
            low,
            close,
            amount,
            turnover_rate,
            LAG(close) OVER (PARTITION BY symbol ORDER BY obs_time) AS prev_close,
            json_extract_string(extra, '$.isST') AS is_st
        FROM equity_daily_bars
        WHERE source_id = ?
          AND obs_time >= '2021-01-01'
        ORDER BY symbol, obs_time
        """,
        [BAOSTOCK_SOURCE_ID],
    ).fetchdf()


def _compute_market_breadth_from_db(conn) -> pd.DataFrame:
    """Compute daily market-breadth factors inside DuckDB.

    The previous implementation loaded all BaoStock bars into pandas before
    aggregating. That works, but it is wasteful during startup refreshes because
    the database already has the window function and group-by machinery needed
    here. Keeping the heavy step in DuckDB makes startup updates much less prone
    to appearing "stuck" on multi-million-row A-share history.
    """
    return conn.execute(
        """
        WITH raw_bars AS (
            SELECT
                symbol,
                obs_time AS date,
                high,
                low,
                close,
                amount,
                turnover_rate,
                LAG(close) OVER (PARTITION BY symbol ORDER BY obs_time) AS prev_close,
                json_extract_string(extra, '$.isST') AS is_st
            FROM equity_daily_bars
            WHERE source_id = ?
              AND obs_time >= DATE '2021-01-01'
        ),
        valid_bars AS (
            SELECT
                *,
                CASE
                    WHEN regexp_matches(symbol, '^(688|689|300|301)') THEN 0.20
                    WHEN regexp_matches(symbol, '^(4|8|92)') THEN 0.30
                    WHEN is_st = '1' THEN 0.05
                    ELSE 0.10
                END AS limit_pct
            FROM raw_bars
            WHERE date IS NOT NULL
              AND symbol IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
              AND prev_close IS NOT NULL
              AND prev_close > 0
        ),
        flags AS (
            SELECT
                date,
                amount,
                turnover_rate,
                close > prev_close AS up_flag,
                close < prev_close AS down_flag,
                close >= (floor(prev_close * (1 + limit_pct) * 100 + 0.5) / 100.0) AS limit_up_flag,
                close <= (floor(prev_close * (1 - limit_pct) * 100 + 0.5) / 100.0) AS limit_down_flag,
                high >= (floor(prev_close * (1 + limit_pct) * 100 + 0.5) / 100.0) AS touch_up_flag
            FROM valid_bars
        ),
        daily_counts AS (
            SELECT
                date,
                SUM(CASE WHEN up_flag THEN 1 ELSE 0 END) AS up_count,
                SUM(CASE WHEN down_flag THEN 1 ELSE 0 END) AS down_count,
                COUNT(*) AS valid_count,
                SUM(CASE WHEN limit_up_flag THEN 1 ELSE 0 END) AS limit_up_count,
                SUM(CASE WHEN limit_down_flag THEN 1 ELSE 0 END) AS limit_down_count,
                SUM(CASE WHEN touch_up_flag THEN 1 ELSE 0 END) AS touch_up_count,
                SUM(CASE WHEN touch_up_flag AND NOT limit_up_flag THEN 1 ELSE 0 END) AS explosive_count
            FROM flags
            GROUP BY date
        ),
        turnover AS (
            SELECT
                date,
                SUM(amount) / NULLIF(SUM(amount / (turnover_rate / 100.0)), 0) * 100.0 AS turnover_rate
            FROM flags
            WHERE amount > 0
              AND turnover_rate > 0
              AND turnover_rate < 100
            GROUP BY date
        ),
        daily_stats AS (
            SELECT
                d.date,
                t.turnover_rate,
                d.up_count - d.down_count AS advance_decline_net,
                d.up_count / NULLIF(d.up_count + d.down_count, 0) AS advance_ratio,
                d.limit_up_count,
                d.limit_down_count,
                d.limit_up_count / NULLIF(d.valid_count, 0) AS limit_up_ratio,
                d.limit_down_count / NULLIF(d.valid_count, 0) AS limit_down_ratio,
                d.explosive_count / NULLIF(d.touch_up_count, 0) AS explosive_ratio
            FROM daily_counts d
            LEFT JOIN turnover t USING (date)
        )
        SELECT
            date,
            turnover_rate,
            advance_decline_net,
            SUM(advance_decline_net) OVER (ORDER BY date) AS advance_decline_line,
            advance_ratio,
            limit_up_count,
            limit_down_count,
            limit_up_ratio,
            limit_down_ratio,
            explosive_ratio
        FROM daily_stats
        ORDER BY date
        """,
        [BAOSTOCK_SOURCE_ID],
    ).fetchdf()


def _limit_percent(symbol: pd.Series, is_st: pd.Series) -> pd.Series:
    code = symbol.astype("string").str.zfill(6)
    is_star = code.str.startswith(("688", "689"))
    is_chinext = code.str.startswith(("300", "301"))
    is_beijing = code.str.startswith(("4", "8", "92"))
    is_mainboard_st = is_st.astype("string").eq("1") & ~(is_star | is_chinext | is_beijing)
    pct = pd.Series(0.10, index=symbol.index, dtype="float64")
    pct.loc[is_mainboard_st] = 0.05
    pct.loc[is_star | is_chinext] = 0.20
    pct.loc[is_beijing] = 0.30
    return pct


def _round_price(value: pd.Series) -> pd.Series:
    return ((value * 100) + 0.5).astype("int64") / 100.0


def compute_market_breadth(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "date", "high", "low", "close", "prev_close", "is_st"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"market breadth bars missing columns: {sorted(missing)}")

    frame = bars.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("high", "low", "close", "prev_close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "symbol", "high", "low", "close", "prev_close"])
    frame = frame[frame["prev_close"] > 0]
    if frame.empty:
        return pd.DataFrame(columns=["date", *sorted(TARGET_FACTORS)])

    limit_pct = _limit_percent(frame["symbol"], frame["is_st"])
    up_limit_price = _round_price(frame["prev_close"] * (1 + limit_pct))
    down_limit_price = _round_price(frame["prev_close"] * (1 - limit_pct))

    frame["up_flag"] = frame["close"] > frame["prev_close"]
    frame["down_flag"] = frame["close"] < frame["prev_close"]
    frame["limit_up_flag"] = frame["close"] >= up_limit_price
    frame["limit_down_flag"] = frame["close"] <= down_limit_price
    frame["touch_up_flag"] = frame["high"] >= up_limit_price
    frame["explosive_flag"] = frame["touch_up_flag"] & ~frame["limit_up_flag"]

    grouped = frame.groupby("date", sort=True)
    stats = grouped.agg(
        up_count=("up_flag", "sum"),
        down_count=("down_flag", "sum"),
        valid_count=("close", "size"),
        limit_up_count=("limit_up_flag", "sum"),
        limit_down_count=("limit_down_flag", "sum"),
        touch_up_count=("touch_up_flag", "sum"),
        explosive_count=("explosive_flag", "sum"),
    ).reset_index()
    direction_count = stats["up_count"] + stats["down_count"]
    stats["advance_decline_net"] = stats["up_count"] - stats["down_count"]
    stats["advance_decline_line"] = stats["advance_decline_net"].cumsum()
    stats["advance_ratio"] = stats["up_count"].where(direction_count.gt(0)) / direction_count.where(direction_count.gt(0))
    stats["limit_up_ratio"] = stats["limit_up_count"] / stats["valid_count"].where(stats["valid_count"].gt(0))
    stats["limit_down_ratio"] = stats["limit_down_count"] / stats["valid_count"].where(stats["valid_count"].gt(0))
    stats["explosive_ratio"] = stats["explosive_count"] / stats["touch_up_count"].where(stats["touch_up_count"].gt(0))

    if {"amount", "turnover_rate"}.issubset(frame.columns):
        turnover = frame.copy()
        turnover["amount"] = pd.to_numeric(turnover["amount"], errors="coerce")
        turnover["turnover_rate"] = pd.to_numeric(turnover["turnover_rate"], errors="coerce")
        turnover = turnover[
            turnover["amount"].gt(0)
            & turnover["turnover_rate"].gt(0)
            & turnover["turnover_rate"].lt(100)
        ].copy()
        if not turnover.empty:
            turnover["estimated_float_value"] = turnover["amount"] / (turnover["turnover_rate"] / 100.0)
            weighted = turnover.groupby("date", sort=True).agg(
                turnover_amount=("amount", "sum"),
                estimated_float_value=("estimated_float_value", "sum"),
            )
            weighted["turnover_rate"] = weighted["turnover_amount"] / weighted["estimated_float_value"] * 100.0
            stats = stats.merge(
                weighted[["turnover_rate"]].reset_index(),
                on="date",
                how="left",
            )
        else:
            stats["turnover_rate"] = pd.NA
    else:
        stats["turnover_rate"] = pd.NA

    return stats[
        [
            "date",
            "turnover_rate",
            "advance_decline_net",
            "advance_decline_line",
            "advance_ratio",
            "limit_up_count",
            "limit_down_count",
            "limit_up_ratio",
            "limit_down_ratio",
            "explosive_ratio",
        ]
    ]


def refresh() -> dict:
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_id": DERIVED_SOURCE_ID,
        "success": [],
        "failed": [],
        "skipped": [],
    }
    conn = ensure_db()
    try:
        stats = _compute_market_breadth_from_db(conn)
        if stats.empty:
            report["failed"].append({"error": "no BaoStock daily bars in equity_daily_bars"})
            return report

        indicators_by_factor = _indicators_by_factor()
        indicator_ids = [indicator.id for indicators in indicators_by_factor.values() for indicator in indicators]
        if indicator_ids:
            placeholders = ", ".join("?" for _ in indicator_ids)
            conn.execute(f"DELETE FROM observations WHERE indicator_id IN ({placeholders})", indicator_ids)
        internal_indicator_ids = [payload["indicator_id"] for payload in INTERNAL_FACTORS.values()]
        internal_placeholders = ", ".join("?" for _ in internal_indicator_ids)
        conn.execute(f"DELETE FROM observations WHERE indicator_id IN ({internal_placeholders})", internal_indicator_ids)

        first_date = pd.Timestamp(stats["date"].min()).to_pydatetime()
        for factor, payload in INTERNAL_FACTORS.items():
            frame = stats[["date", factor]].dropna().copy()
            rows = [
                (
                    row.date.to_pydatetime(),
                    float(getattr(row, factor)),
                    {
                        "source": DERIVED_SOURCE_ID,
                        "calculation": payload["calculation"],
                    },
                )
                for row in frame.itertuples(index=False)
            ]
            replace_observations_since(
                conn,
                payload["indicator_id"],
                payload["series_id"],
                DERIVED_SOURCE_ID,
                rows,
                since=first_date,
                unit=payload["unit"],
            )
            report["success"].append({
                "factor": factor,
                "indicator_id": payload["indicator_id"],
                "rows": len(frame),
                "date_min": frame["date"].min().date().isoformat(),
                "date_max": frame["date"].max().date().isoformat(),
                "db_only": True,
            })
        for factor in sorted(TARGET_FACTORS):
            indicators = indicators_by_factor.get(factor, [])
            if not indicators:
                report["skipped"].append({"factor": factor, "reason": "factor is not present in indicator catalog"})
                continue
            frame = stats[["date", factor]].dropna().copy()
            if frame.empty:
                report["skipped"].append({"factor": factor, "reason": "factor has no valid observations"})
                continue
            rows = [
                (
                    row.date.to_pydatetime(),
                    float(getattr(row, factor)),
                    {
                        "source": DERIVED_SOURCE_ID,
                        "calculation": (
                            "BaoStock OHLC daily market breadth"
                            if factor != "turnover_rate"
                            else "sum(amount) / sum(amount / (individual turnover_rate / 100)) * 100"
                        ),
                    },
                )
                for row in frame.itertuples(index=False)
            ]
            for indicator in indicators:
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
                chart["source_label"] = "BaoStock daily OHLC + derived"
                html_path, image_path = render_chart(
                    frame,
                    replace(indicator, chart=chart),
                    PROJECT_ROOT / "charts" / indicator.id,
                )
                record_chart(
                    conn,
                    indicator.id,
                    html_path,
                    image_path,
                    (
                        "BaoStock daily-all OHLC; calculated market breadth and price-limit statistics"
                        if factor != "turnover_rate"
                        else "BaoStock daily-all amount and individual turnover rates; calculated float-value-weighted market turnover"
                    ),
                )
                report["success"].append({
                    "factor": factor,
                    "indicator_id": indicator.id,
                    "rows": len(frame),
                    "date_min": frame["date"].min().date().isoformat(),
                    "date_max": frame["date"].max().date().isoformat(),
                })
        report["factor_rows"] = len(stats)
    except Exception as exc:
        report["failed"].append({"error": str(exc)})
        raise
    finally:
        conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/baostock_market_breadth_refresh.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    print(json.dumps(refresh(), ensure_ascii=False, indent=2))
