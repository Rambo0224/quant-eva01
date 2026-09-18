from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.akshare_public import (
    AkshareSourceUnavailable,
    fetch_50etf_ivix_history,
    fetch_50etf_option_pcr,
    fetch_50etf_qvix_history,
    fetch_trade_dates,
)
from macro_replay.charts import render_chart
from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, insert_missing_observations, record_chart
from macro_replay.source_labels import display_source_label


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _indicator_by_factor(factor: str):
    for indicator in load_indicators():
        if indicator.metadata.get("factor_name") == factor:
            return indicator
    raise KeyError(f"No catalog indicator found for {factor}")


def _store_series(conn, factor: str, frame: pd.DataFrame, endpoint: str, note: str) -> dict:
    indicator = _indicator_by_factor(factor)
    rows = [
        (
            pd.Timestamp(row["date"]).to_pydatetime(),
            float(row[factor]),
            {"endpoint": endpoint, "note": note},
        )
        for _, row in frame[["date", factor]].dropna().iterrows()
    ]
    if not rows:
        return {"factor": factor, "rows": 0}
    inserted = insert_missing_observations(
        conn,
        indicator.id,
        f"{indicator.id}:{factor}",
        "akshare_public",
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
        [indicator.id, f"{indicator.id}:{factor}"],
    ).df().rename(columns={"value": factor})
    chart = dict(indicator.chart or {})
    chart["source_label"] = "akshare_public"
    html_path, image_path = render_chart(
        stored[["date", factor]],
        replace(indicator, chart=chart),
        PROJECT_ROOT / "charts" / indicator.id,
    )
    record_chart(
        conn,
        indicator.id,
        html_path,
        image_path,
        f"{display_source_label('akshare_public')} / {endpoint}",
    )
    return {
        "factor": factor,
        "rows": len(stored),
        "inserted": inserted,
        "date_min": str(stored["date"].min().date()),
        "date_max": str(stored["date"].max().date()),
    }


def _existing_dates(conn, factor: str) -> set[pd.Timestamp]:
    indicator = _indicator_by_factor(factor)
    rows = conn.execute(
        "SELECT obs_time FROM observations WHERE indicator_id = ? AND series_id = ?",
        [indicator.id, f"{indicator.id}:{factor}"],
    ).fetchall()
    return {pd.Timestamp(row[0]).normalize() for row in rows}


def _existing_factor_frame(conn, source_factor: str, target_factor: str) -> pd.DataFrame:
    indicator = _indicator_by_factor(source_factor)
    rows = conn.execute(
        """
        SELECT obs_time AS date, value
        FROM observations
        WHERE indicator_id = ? AND series_id = ?
        ORDER BY obs_time
        """,
        [indicator.id, f"{indicator.id}:{source_factor}"],
    ).df()
    if rows.empty:
        return pd.DataFrame(columns=["date", target_factor])
    rows["date"] = pd.to_datetime(rows["date"], errors="coerce")
    rows[target_factor] = pd.to_numeric(rows.pop("value"), errors="coerce")
    return rows.dropna(subset=["date", target_factor]).reset_index(drop=True)


def _fetch_pcr_history(
    start_date: str,
    end_date: str,
    existing_dates: set[pd.Timestamp],
    batch_size: int = 20,
    workers: int = 3,
    candidate_dates=None,
) -> tuple[pd.DataFrame, list[dict]]:
    source_dates = candidate_dates if candidate_dates is not None else fetch_trade_dates(start_date, end_date)
    dates = [day for day in source_dates if day.normalize() not in existing_dates]
    frames: list[pd.DataFrame] = []
    failures: list[dict] = []

    def fetch_one(day: pd.Timestamp):
        date_text = day.date().isoformat()
        try:
            return date_text, fetch_50etf_option_pcr(date_text), None
        except (AkshareSourceUnavailable, Exception) as exc:  # pragma: no cover - live endpoint
            return date_text, pd.DataFrame(), str(exc)

    for offset in range(0, len(dates), batch_size):
        batch = dates[offset : offset + batch_size]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch_one, day) for day in batch]
            for future in as_completed(futures):
                date_text, frame, error = future.result()
                if error:
                    failures.append({"date": date_text, "error": error})
                elif not frame.empty:
                    frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["date", "pcr_volume", "pcr_oi"]), failures
    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True), failures


def refresh(start_date: str, end_date: str, batch_size: int = 20, workers: int = 3) -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    conn = ensure_db()
    try:
        qvix_indicator = _indicator_by_factor("qvix")
        qvix_existing = _existing_dates(conn, "qvix")
        qvix_min = min(qvix_existing) if qvix_existing else None
        qvix_max = max(qvix_existing) if qvix_existing else None
        qvix_missing_boundary = (
            not qvix_existing
            or pd.Timestamp(start_date).normalize() < qvix_min
            or pd.Timestamp(end_date).normalize() > qvix_max
        )
        if qvix_missing_boundary:
            qvix = fetch_50etf_qvix_history(start_date=start_date, end_date=end_date)
            report["success"].append(
                _store_series(conn, "qvix", qvix.frame, qvix.endpoint, qvix.note)
            )
        else:
            report["skipped"].append({"factor": "qvix", "reason": "requested dates already exist in database"})
    except Exception as exc:
        report["failed"].append({"factor": "qvix", "error": str(exc)})

    try:
        ivix_existing = _existing_dates(conn, "ivix_50")
        ivix_min = min(ivix_existing) if ivix_existing else None
        ivix_max = max(ivix_existing) if ivix_existing else None
        ivix_missing_boundary = (
            not ivix_existing
            or pd.Timestamp(start_date).normalize() < ivix_min
            or pd.Timestamp(end_date).normalize() > ivix_max
        )
        if ivix_missing_boundary:
            qvix_frame = _existing_factor_frame(conn, "qvix", "ivix_50")
            qvix_frame = qvix_frame[
                (qvix_frame["date"] >= pd.Timestamp(start_date))
                & (qvix_frame["date"] <= pd.Timestamp(end_date))
            ]
            if not qvix_frame.empty:
                report["success"].append(
                    _store_series(
                        conn,
                        "ivix_50",
                        qvix_frame,
                        "index_option_50etf_qvix",
                        "Reused verified AkShare 50ETF QVIX observations as the 50ETF implied-volatility series",
                    )
                )
            else:
                ivix = fetch_50etf_ivix_history(start_date=start_date, end_date=end_date)
                report["success"].append(
                    _store_series(conn, "ivix_50", ivix.frame, ivix.endpoint, ivix.note)
                )
        else:
            report["skipped"].append({"factor": "ivix_50", "reason": "requested dates already exist in database"})
    except Exception as exc:
        report["failed"].append({"factor": "ivix_50", "error": str(exc)})

    pcr_existing = _existing_dates(conn, "pcr_volume")
    pcr_trade_dates = fetch_trade_dates(start_date, end_date)
    pcr_missing = [day for day in pcr_trade_dates if day.normalize() not in pcr_existing]
    if not pcr_missing:
        report["skipped"].append({"factor": "pcr_volume/pcr_oi", "reason": "official trading dates already exist in database"})
    else:
        pcr, failures = _fetch_pcr_history(
            start_date,
            end_date,
            pcr_existing,
            batch_size=batch_size,
            workers=workers,
            candidate_dates=pcr_missing,
        )
        if failures:
            report["skipped"].append({"factor": "pcr_volume/pcr_oi", "failed_dates": len(failures), "sample": failures[:5], "dates": failures})
        if not pcr.empty:
            for factor in ("pcr_volume", "pcr_oi"):
                report["success"].append(
                    _store_series(
                        conn,
                        factor,
                        pcr,
                        "option_daily_stats_sse",
                        "SSE 50ETF daily option statistics; put volume/open interest divided by call volume/open interest",
                    )
                )
        elif not failures:
            report["failed"].append({"factor": "pcr_volume/pcr_oi", "error": "no valid 50ETF option statistics returned"})
    conn.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/akshare_recovered_refresh.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh recovered AkShare QVIX and 50ETF PCR history")
    parser.add_argument("--start", default="2015-02-09")
    parser.add_argument("--end", default=pd.Timestamp.today().date().isoformat())
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(refresh(args.start, args.end, args.batch_size, args.workers), ensure_ascii=False, indent=2))
