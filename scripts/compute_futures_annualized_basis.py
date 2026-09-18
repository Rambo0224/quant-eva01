from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.cffex import (
    CffexSourceUnavailable,
    days_to_expiry,
    fetch_cffex_history,
    index_futures_expiry_date,
)
from datahub.transforms.ashare import annualized_basis_rate
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, insert_missing_observations, record_chart


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATCH_TOLERANCE = 1e-6
SERIES = {
    "IF": {
        "future_indicator": "if-close",
        "future_field": "if_close",
        "spot_indicator": "hs300-close",
        "annual_indicator": "if-basis-annual",
        "annual_field": "if_basis_annual",
    },
    "IC": {
        "future_indicator": "ic-close",
        "future_field": "ic_close",
        "spot_indicator": "zz500-close",
        "annual_indicator": "ic-basis-annual",
        "annual_field": "ic_basis_annual",
    },
}


def _indicator_by_id(indicator_id: str):
    for indicator in load_indicators():
        if indicator.id == indicator_id:
            return indicator
    raise KeyError(f"Unknown indicator: {indicator_id}")


def _load_trade_dates() -> pd.DatetimeIndex:
    path = PROJECT_ROOT / "data" / "baostock_trade_dates.csv"
    if not path.exists():
        return pd.DatetimeIndex([])
    frame = pd.read_csv(path)
    if {"calendar_date", "is_trading_day"}.issubset(frame.columns):
        frame = frame[pd.to_numeric(frame["is_trading_day"], errors="coerce").fillna(0).astype(int).eq(1)]
        dates = pd.to_datetime(frame["calendar_date"], errors="coerce").dropna()
        return pd.DatetimeIndex(dates).normalize().drop_duplicates().sort_values()
    return pd.DatetimeIndex([])


def _current_observation_bounds(conn) -> tuple[str, str] | None:
    row = conn.execute(
        """
        SELECT min(obs_time), max(obs_time)
        FROM observations
        WHERE indicator_id IN ('if-close', 'ic-close')
        """
    ).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    return pd.Timestamp(row[0]).date().isoformat(), pd.Timestamp(row[1]).date().isoformat()


def _load_series(conn, futures_id: str, future_field: str, spot_id: str) -> pd.DataFrame:
    futures = conn.execute(
        """
        SELECT cast(obs_time AS date) AS date, value AS future_close
        FROM observations
        WHERE indicator_id = ?
        QUALIFY row_number() OVER(PARTITION BY obs_time ORDER BY ingested_at DESC,source_id)=1
        ORDER BY obs_time
        """,
        [futures_id],
    ).df()
    spot = conn.execute(
        """
        SELECT cast(obs_time AS date) AS date, value AS spot_close
        FROM observations
        WHERE indicator_id = ?
        QUALIFY row_number() OVER(PARTITION BY obs_time ORDER BY ingested_at DESC,source_id)=1
        ORDER BY obs_time
        """,
        [spot_id],
    ).df()
    if futures.empty or spot.empty:
        return pd.DataFrame(columns=["date", future_field, "spot_close"])
    futures["date"] = pd.to_datetime(futures["date"], errors="coerce")
    spot["date"] = pd.to_datetime(spot["date"], errors="coerce")
    futures = futures.rename(columns={"future_close": future_field})
    return futures.merge(spot, on="date", how="inner").dropna(subset=[future_field, "spot_close"])


def fetch_contract_table_json(start, end, batch_days):
    return {'frame':fetch_cffex_history(start,end,batch_days=batch_days).frame.to_json(orient='table',date_format='iso')}


def _fetch_contract_table(start: str, end: str, batch_days: int) -> tuple[pd.DataFrame, str | None]:
    from datahub.sync.isolated import call
    from io import StringIO
    result=call('scripts.compute_futures_annualized_basis','fetch_contract_table_json',(start,end,batch_days),timeout=150)
    if result.get('failed'):
        return pd.DataFrame(columns=['date','variety','symbol','close']),str(result['failed'])
    return pd.read_json(StringIO(result['frame']),orient='table'),None


def _front_month_symbol(variety: str, trade_date: pd.Timestamp, trade_dates: pd.DatetimeIndex) -> str:
    date = pd.Timestamp(trade_date).normalize()
    month = pd.Timestamp(year=date.year, month=date.month, day=1)
    for _ in range(24):
        symbol = f"{variety}{str(month.year)[-2:]}{month.month:02d}"
        if index_futures_expiry_date(symbol, trade_dates) >= date:
            return symbol
        month = month + pd.DateOffset(months=1)
    raise ValueError(f"Could not infer front-month contract for {variety} on {date.date()}")


def _infer_front_month_contracts(
    series: pd.DataFrame,
    variety: str,
    trade_dates: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, list[dict]]:
    if series.empty:
        return pd.DataFrame(), [{"variety": variety, "reason": "missing futures observations or spot-index observations"}]
    inferred = series.copy()
    inferred["symbol"] = inferred["date"].map(lambda date: _front_month_symbol(variety, pd.Timestamp(date), trade_dates))
    inferred["expiry_date"] = inferred["symbol"].map(lambda symbol: index_futures_expiry_date(symbol, trade_dates))
    inferred["days_to_expiry"] = inferred.apply(
        lambda row: days_to_expiry(pd.Timestamp(row["date"]), str(row["symbol"]), trade_dates),
        axis=1,
    )
    inferred = inferred[inferred["days_to_expiry"].gt(0)].copy()
    skipped = len(series) - len(inferred)
    failures = [
        {
            "variety": variety,
            "expiry_dates_skipped": skipped,
            "reason": "front-month contract has zero days to expiry on delivery dates",
        }
    ] if skipped else []
    return inferred.sort_values("date").reset_index(drop=True), failures


def _match_contracts(
    series: pd.DataFrame,
    contract_table: pd.DataFrame,
    variety: str,
    future_field: str,
    trade_dates: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, list[dict]]:
    if series.empty or contract_table.empty:
        return pd.DataFrame(), [{"variety": variety, "reason": "missing futures observations or CFFEX contract table"}]

    candidates = contract_table[contract_table["variety"].astype("string").str.upper() == variety].copy()
    if candidates.empty:
        return pd.DataFrame(), [{"variety": variety, "reason": "no matching CFFEX variety rows"}]
    candidates["date"] = pd.to_datetime(candidates["date"], errors="coerce")
    candidates["close"] = pd.to_numeric(candidates["close"], errors="coerce")
    candidates = candidates.dropna(subset=["date", "symbol", "close"])

    joined = series.merge(candidates[["date", "symbol", "close"]], on="date", how="left")
    joined["price_diff"] = (pd.to_numeric(joined[future_field], errors="coerce") - joined["close"]).abs()
    matched = joined[joined["price_diff"].le(MATCH_TOLERANCE)].copy()
    if matched.empty:
        return pd.DataFrame(), [{"variety": variety, "reason": "stored futures close did not match fetched CFFEX contract closes"}]

    matched["expiry_date"] = matched["symbol"].map(lambda symbol: index_futures_expiry_date(symbol, trade_dates))
    matched["days_to_expiry"] = matched.apply(
        lambda row: days_to_expiry(pd.Timestamp(row["date"]), str(row["symbol"]), trade_dates),
        axis=1,
    )
    matched = matched.sort_values(["date", "days_to_expiry", "symbol"]).drop_duplicates("date", keep="first")
    matched = matched[matched["days_to_expiry"].gt(0)].copy()
    unmatched_dates = set(pd.to_datetime(series["date"]).dt.normalize()) - set(pd.to_datetime(matched["date"]).dt.normalize())
    failures = [
        {
            "variety": variety,
            "unmatched_or_expiry_dates": len(unmatched_dates),
            "sample": [str(day.date()) for day in sorted(unmatched_dates)[:8]],
        }
    ] if unmatched_dates else []
    return matched.sort_values("date").reset_index(drop=True), failures


def _store_annualized(conn, indicator_id: str, field: str, frame: pd.DataFrame, note: str) -> dict:
    indicator = _indicator_by_id(indicator_id)
    rows = []
    for row in frame.itertuples(index=False):
        rows.append(
            (
                pd.Timestamp(row.date).to_pydatetime(),
                float(getattr(row, field)),
                {
                    "note": note,
                    "symbol": str(row.symbol),
                    "expiry_date": str(pd.Timestamp(row.expiry_date).date()),
                    "days_to_expiry": int(row.days_to_expiry),
                },
            )
        )
    if not rows:
        return {"indicator_id": indicator_id, "rows": 0, "inserted": 0}
    inserted = insert_missing_observations(
        conn,
        indicator.id,
        f"{indicator.id}:{field}",
        "derived",
        rows,
        unit=(indicator.chart or {}).get("unit"),
    )
    stored = conn.execute(
        """
        SELECT obs_time AS date, value AS value
        FROM observations
        WHERE indicator_id = ? AND series_id = ?
        ORDER BY obs_time
        """,
        [indicator.id, f"{indicator.id}:{field}"],
    ).df().rename(columns={"value": field})
    chart = dict(indicator.chart or {})
    chart["source_label"] = "derived"
    html_path, image_path = render_chart(
        stored[["date", field]],
        replace(indicator, chart=chart),
        PROJECT_ROOT / "charts" / indicator.id,
    )
    record_chart(conn, indicator.id, html_path, image_path, f"derived / {note}")
    return {
        "indicator_id": indicator.id,
        "rows": len(stored),
        "inserted": inserted,
        "date_min": str(stored["date"].min().date()),
        "date_max": str(stored["date"].max().date()),
        "html_path": str(html_path),
        "image_path": str(image_path),
    }


def refresh(
    start: str | None = None,
    end: str | None = None,
    batch_days: int = 20,
    use_official_match: bool = True,
) -> dict:
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    conn = ensure_db()
    try:
        bounds = _current_observation_bounds(conn)
        if bounds is None:
            report["failed"].append({"reason": "if-close/ic-close observations are missing"})
            return report
        if start is None:
            previous=conn.execute("SELECT min(latest) FROM (SELECT indicator_id,max(obs_time) latest FROM observations WHERE indicator_id IN ('if-basis-annual','ic-basis-annual') GROUP BY 1)").fetchone()[0]
            start=str(pd.Timestamp(previous).date()) if previous else bounds[0]
        end = end or bounds[1]
        trade_dates = _load_trade_dates()
        contract_table = pd.DataFrame(columns=["date", "variety", "symbol", "close"])
        if use_official_match:
            contract_table, error = _fetch_contract_table(start, end, batch_days=batch_days)
            if error:
                report['failed'].append({'endpoint':'get_futures_daily','error':error,'reason':'contract identity cannot be verified; previous values retained'})
                return report
            if contract_table.empty:
                report['failed'].append({'reason':'No official contract table; expiry cannot be verified'})
                return report

        for variety, cfg in SERIES.items():
            series = _load_series(conn, cfg["future_indicator"], cfg["future_field"], cfg["spot_indicator"])
            series = series[(series["date"] >= pd.Timestamp(start)) & (series["date"] <= pd.Timestamp(end))]
            if use_official_match and not contract_table.empty:
                matched, failures = _match_contracts(series, contract_table, variety, cfg["future_field"], trade_dates)
                source_note = f"{variety} futures close matched to CFFEX contract code; annualized by calendar days to expiry"
            else:
                matched, failures = _infer_front_month_contracts(series, variety, trade_dates)
                source_note = f"{variety} futures/index closes from stored real observations; front-month contract inferred from CFFEX expiry schedule and annualized by calendar days to expiry"
            if failures:
                report["skipped"].extend(failures)
            if matched.empty:
                report["failed"].append({"indicator_id": cfg["annual_indicator"], "reason": "no positive days-to-expiry matches"})
                continue
            matched[cfg["annual_field"]] = (
                annualized_basis_rate(
                    pd.to_numeric(matched[cfg["future_field"]], errors="coerce"),
                    pd.to_numeric(matched["spot_close"], errors="coerce"),
                    pd.to_numeric(matched["days_to_expiry"], errors="coerce"),
                )
                * 100.0
            )
            annual_frame = matched[["date", cfg["annual_field"], "symbol", "expiry_date", "days_to_expiry"]].dropna()
            report["success"].append(
                _store_annualized(
                    conn,
                    cfg["annual_indicator"],
                    cfg["annual_field"],
                    annual_frame,
                    source_note,
                )
            )
    finally:
        conn.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (PROJECT_ROOT / "logs").mkdir(exist_ok=True)
        (PROJECT_ROOT / "logs" / "futures_annualized_basis_refresh.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute IF/IC annualized basis from stored futures/index prices.")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--batch-days", type=int, default=20)
    parser.add_argument("--use-official-match", action="store_true")
    args = parser.parse_args()
    print(json.dumps(refresh(args.start, args.end, args.batch_days, args.use_official_match), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
