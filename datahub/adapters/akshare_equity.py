from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


class AkShareEquityUnavailable(RuntimeError):
    """Raised when AkShare cannot return a usable A-share history."""


@dataclass(frozen=True)
class AkShareEquityFetchResult:
    frame: pd.DataFrame
    source_id: str
    endpoint: str
    fetched_at: datetime
    note: str = ""


SOURCE_ID = "akshare_stock_zh_a_hist_qfq"
ENDPOINT = "ak.stock_zh_a_hist"


def fetch_a_share_universe(refresh: bool = False) -> pd.DataFrame:
    """Return the current A-share stock universe from AkShare's exchange lists."""
    cache_path = Path("data") / "ashare_universe.csv"
    if cache_path.exists() and not refresh:
        cached = pd.read_csv(cache_path, dtype={"code": "string"})
        if {"code", "name"}.issubset(cached.columns) and not cached.empty:
            return cached[["code", "name"]].copy()
    try:
        import akshare as ak

        frame = ak.stock_info_a_code_name()
    except Exception as exc:  # pragma: no cover - depends on remote source
        if cache_path.exists():
            cached = pd.read_csv(cache_path, dtype={"code": "string"})
            if {"code", "name"}.issubset(cached.columns) and not cached.empty:
                return cached[["code", "name"]].copy()
        raise AkShareEquityUnavailable(f"{ENDPOINT} stock universe failed and no cache is available: {exc}") from exc

    required = {"code", "name"}
    missing = required.difference(frame.columns)
    if missing:
        raise AkShareEquityUnavailable(f"stock universe missing columns: {sorted(missing)}")
    result = frame[["code", "name"]].copy()
    result["code"] = result["code"].astype("string").str.zfill(6)
    result = result[result["code"].str.fullmatch(r"\d{6}")]
    result = result.drop_duplicates("code").sort_values("code").reset_index(drop=True)
    cache_path.parent.mkdir(exist_ok=True)
    result.to_csv(cache_path, index=False, encoding="utf-8-sig")
    return result


def normalize_equity_history(frame: pd.DataFrame, symbol: str, adjust: str = "qfq") -> pd.DataFrame:
    mapping = {
        "日期": "date",
        "股票代码": "symbol",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "换手率": "turnover_rate",
    }
    normalized = frame.rename(columns=mapping).copy()
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required.difference(normalized.columns)
    if missing:
        raise AkShareEquityUnavailable(f"{ENDPOINT} missing columns: {sorted(missing)}")
    normalized["symbol"] = str(symbol).zfill(6)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=["date", "open", "high", "low", "close"])
    normalized["price_adjustment"] = adjust or "none"
    return normalized[
        [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover_rate",
            "price_adjustment",
        ]
    ].sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_equity_history(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> AkShareEquityFetchResult:
    """Fetch one stock's full daily history for a requested range."""
    try:
        import akshare as ak

        raw = ak.stock_zh_a_hist(
            symbol=str(symbol).zfill(6),
            period="daily",
            start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
            end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
            adjust=adjust,
        )
    except Exception as exc:  # pragma: no cover - depends on remote source
        raise AkShareEquityUnavailable(f"{ENDPOINT} failed for {symbol}: {exc}") from exc
    frame = normalize_equity_history(raw, symbol, adjust)
    if frame.empty:
        raise AkShareEquityUnavailable(f"{ENDPOINT} returned no rows for {symbol}")
    return AkShareEquityFetchResult(
        frame=frame,
        source_id=SOURCE_ID,
        endpoint=ENDPOINT,
        fetched_at=datetime.now(timezone.utc),
        note="AkShare official stock_zh_a_hist daily OHLCV with forward-adjusted prices",
    )
