from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os

import pandas as pd


class TushareSourceUnavailable(RuntimeError):
    """Raised when Tushare Pro credentials or a required field are unavailable."""


@dataclass(frozen=True)
class TushareFetchResult:
    frame: pd.DataFrame
    source_id: str
    endpoint: str
    fetched_at: datetime
    note: str = ""


def _client(token: str | None = None, pro=None):
    if pro is not None:
        return pro
    credential = token or os.getenv("TUSHARE_TOKEN", "").strip()
    if not credential:
        raise TushareSourceUnavailable(
            "Tushare Pro token is not configured; set TUSHARE_TOKEN before fetching"
        )
    try:
        import tushare as ts

        return ts.pro_api(credential)
    except Exception as exc:  # pragma: no cover - optional dependency
        raise TushareSourceUnavailable(f"Tushare Pro client initialization failed: {exc}") from exc


def _normalize_daily(raw: pd.DataFrame, endpoint: str, symbol: str | None = None) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close", "vol"}
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    missing = required.difference(raw.columns)
    if missing:
        raise TushareSourceUnavailable(f"{endpoint} missing columns: {sorted(missing)}")
    columns = ["trade_date", "open", "high", "low", "close", "vol"]
    if "ts_code" in raw.columns:
        columns.insert(0, "ts_code")
    frame = raw[columns].rename(columns={"trade_date": "date", "vol": "volume"}).copy()
    frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["symbol"] = frame.pop("ts_code") if "ts_code" in frame.columns else symbol
    return frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


def fetch_equity_history(
    ts_code: str,
    start_date: str,
    end_date: str | None = None,
    token: str | None = None,
    pro=None,
) -> TushareFetchResult:
    """Fetch one Tushare Pro daily equity series; token is intentionally required."""
    endpoint = "daily"
    api = _client(token, pro)
    try:
        raw = api.daily(
            ts_code=ts_code,
            start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
            end_date=pd.Timestamp(end_date or pd.Timestamp.today()).strftime("%Y%m%d"),
        )
    except Exception as exc:  # pragma: no cover - depends on Tushare service
        raise TushareSourceUnavailable(f"{endpoint} failed for {ts_code}: {exc}") from exc
    frame = _normalize_daily(raw, endpoint, ts_code)
    if frame.empty:
        raise TushareSourceUnavailable(f"{endpoint} returned no rows for {ts_code}")
    return TushareFetchResult(
        frame=frame,
        source_id="tushare_pro",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="Tushare Pro daily OHLCV; token-authenticated source",
    )


def fetch_market_daily(
    start_date: str,
    end_date: str,
    token: str | None = None,
    pro=None,
) -> TushareFetchResult:
    """Fetch one bounded all-market daily batch for later cross-sectional factors."""
    endpoint = "daily"
    api = _client(token, pro)
    try:
        raw = api.daily(
            start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
            end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
        )
    except Exception as exc:  # pragma: no cover - depends on Tushare service
        raise TushareSourceUnavailable(f"{endpoint} market batch failed: {exc}") from exc
    frame = _normalize_daily(raw, endpoint)
    if frame.empty:
        raise TushareSourceUnavailable(f"{endpoint} returned no market rows for {start_date}..{end_date}")
    return TushareFetchResult(
        frame=frame,
        source_id="tushare_pro",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="Tushare Pro all-market daily batch; request windows must be kept bounded by the caller",
    )
