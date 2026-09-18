from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from datahub.adapters.openbb_yfinance import normalize_openbb_symbol


SOURCE_ID = "openbb_yfinance_batch"


def fetch_histories(tickers: list[str], start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
    """Download a bounded batch of Yahoo Finance daily OHLCV histories."""
    if not tickers:
        return {}
    try:
        import yfinance as yf

        raw = yf.download(
            tickers=tickers,
            start=pd.Timestamp(start_date).date().isoformat(),
            end=pd.Timestamp(end_date).date().isoformat(),
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
            timeout=20,
        )
    except Exception as exc:  # pragma: no cover - depends on remote provider
        raise RuntimeError(f"Yahoo Finance batch download failed: {exc}") from exc
    if raw is None or raw.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}
    fields = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    if isinstance(raw.columns, pd.MultiIndex):
        available = set(raw.columns.get_level_values(0))
        ticker_level = 0
        if not any(ticker in available for ticker in tickers):
            ticker_level = 1
        available_tickers = set(raw.columns.get_level_values(ticker_level))
        for ticker in tickers:
            if ticker not in available_tickers:
                continue
            try:
                frame = raw[ticker] if ticker_level == 0 else raw.xs(ticker, axis=1, level=ticker_level)
            except KeyError:
                continue
            frame = frame.rename(columns=fields)
            if not set(fields.values()).issubset(frame.columns):
                continue
            normalized = frame[list(fields.values())].reset_index().rename(columns={"Date": "date"})
            normalized["symbol"] = ticker
            result[ticker] = _normalize(normalized)
    else:
        # yfinance uses a flat frame for a single ticker.
        frame = raw.rename(columns=fields)
        if set(fields.values()).issubset(frame.columns):
            normalized = frame[list(fields.values())].reset_index().rename(columns={"Date": "date"})
            normalized["symbol"] = tickers[0]
            result[tickers[0]] = _normalize(normalized)
    return result


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
    frame["symbol"] = frame["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
    return frame[["symbol", "date", "open", "high", "low", "close", "volume"]].sort_values("date").drop_duplicates("date")
