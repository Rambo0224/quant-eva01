from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd


class OpenBBSourceUnavailable(RuntimeError):
    """Raised when the optional OpenBB Yahoo Finance provider cannot return data."""


@dataclass(frozen=True)
class OpenBBFetchResult:
    frame: pd.DataFrame
    source_id: str
    endpoint: str
    fetched_at: datetime
    note: str = ""


def normalize_openbb_symbol(symbol: str) -> str:
    """Map a Chinese A-share code to Yahoo Finance's exchange suffix."""
    value = str(symbol).strip().upper()
    if "." in value:
        return value
    if value.startswith(("SH", "SZ", "BJ")) and value[2:].isdigit():
        value = value[2:]
    if value.startswith(("6", "68")):
        return f"{value}.SS"
    if value.startswith(("0", "2", "3")):
        return f"{value}.SZ"
    if value.startswith(("4", "8")):
        return f"{value}.BJ"
    return value


def fetch_equity_history(
    symbol: str,
    start_date: str,
    end_date: str | None = None,
) -> OpenBBFetchResult:
    """Fetch one A-share daily OHLCV series through OpenBB's YFinance provider."""
    endpoint = "openbb_yfinance.equity_historical"
    try:
        from openbb_yfinance.models.equity_historical import YFinanceEquityHistoricalFetcher
    except Exception as exc:  # pragma: no cover - optional dependency
        raise OpenBBSourceUnavailable(
            "OpenBB YFinance provider is not installed; install openbb-yfinance"
        ) from exc

    mapped_symbol = normalize_openbb_symbol(symbol)
    try:
        query = YFinanceEquityHistoricalFetcher.transform_query(
            {
                "symbol": mapped_symbol,
                "start_date": pd.Timestamp(start_date).date(),
                "end_date": pd.Timestamp(end_date or pd.Timestamp.today()).date(),
                "interval": "1d",
            }
        )
        raw = YFinanceEquityHistoricalFetcher.extract_data(query, credentials=None)
        records = YFinanceEquityHistoricalFetcher.transform_data(query, raw)
    except Exception as exc:  # pragma: no cover - depends on remote provider
        raise OpenBBSourceUnavailable(f"{endpoint} failed for {mapped_symbol}: {exc}") from exc

    frame = pd.DataFrame([record.model_dump() for record in records])
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise OpenBBSourceUnavailable(f"{endpoint} missing columns: {sorted(missing)}")
    frame = frame[["date", "open", "high", "low", "close", "volume"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in frame.columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
    frame["symbol"] = mapped_symbol
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if frame.empty:
        raise OpenBBSourceUnavailable(f"{endpoint} returned no rows for {mapped_symbol}")
    return OpenBBFetchResult(
        frame=frame,
        source_id="openbb_yfinance",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="OpenBB standard equity historical model backed by Yahoo Finance; candidate public fallback for A-share OHLCV",
    )
