from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable

import akshare as ak
import pandas as pd


class CffexSourceUnavailable(RuntimeError):
    """Raised when the CFFEX daily endpoint is unavailable or changes shape."""


@dataclass(frozen=True)
class CffexFetchResult:
    frame: pd.DataFrame
    source_id: str
    endpoint: str
    fetched_at: datetime
    note: str = ""


REQUIRED_COLUMNS = {"symbol", "date", "close", "volume", "open_interest", "variety"}
VARIETIES = ("IF", "IC", "IH")


def normalize_cffex_daily(raw: pd.DataFrame, date: str | None = None) -> pd.DataFrame:
    """Normalize AkShare's CFFEX daily table and retain index futures only."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=sorted(REQUIRED_COLUMNS))

    missing = REQUIRED_COLUMNS.difference(raw.columns)
    if missing:
        raise CffexSourceUnavailable(f"CFFEX daily table missing columns: {sorted(missing)}")

    frame = raw[list(REQUIRED_COLUMNS)].copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.upper()
    frame["variety"] = frame["variety"].astype("string").str.strip().str.upper()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("close", "volume", "open_interest"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[frame["variety"].isin(VARIETIES)]
    frame = frame.dropna(subset=["symbol", "date", "close"])
    if date:
        frame = frame[frame["date"] == pd.Timestamp(date)]
    return frame.sort_values(["date", "variety", "symbol"]).reset_index(drop=True)


def fetch_cffex_daily(date: str, timeout: float = 20.0, retries: int = 2) -> CffexFetchResult:
    endpoint = "get_cffex_daily"
    compact_date = pd.Timestamp(date).strftime("%Y%m%d")
    try:
        raw = ak.get_cffex_daily(date=compact_date)
    except Exception as exc:  # pragma: no cover - depends on live official endpoint
        raise CffexSourceUnavailable(f"{endpoint} failed for {compact_date}: {exc}") from exc
    frame = normalize_cffex_daily(raw, compact_date)
    if frame.empty:
        raise CffexSourceUnavailable(f"{endpoint} returned no IF/IC/IH rows for {compact_date}")
    return CffexFetchResult(
        frame=frame,
        source_id="cffex_official",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="AkShare get_cffex_daily; main contract is selected by daily volume, then open interest",
    )


def fetch_cffex_history(start_date: str, end_date: str, batch_days: int = 20) -> CffexFetchResult:
    """Fetch a date range through AkShare's official CFFEX range endpoint."""
    endpoint = "get_futures_daily"
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if start > end:
        raise ValueError("start_date must not be later than end_date")
    if batch_days < 1:
        raise ValueError("batch_days must be positive")

    chunks: list[pd.DataFrame] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + pd.Timedelta(days=batch_days - 1), end)
        try:
            raw = ak.get_futures_daily(
                start_date=chunk_start.strftime("%Y%m%d"),
                end_date=chunk_end.strftime("%Y%m%d"),
                market="CFFEX",
            )
        except Exception as exc:  # pragma: no cover - depends on live official endpoint
            raise CffexSourceUnavailable(
                f"{endpoint} failed for {chunk_start.date()}..{chunk_end.date()}: {exc}"
            ) from exc
        frame = normalize_cffex_daily(raw)
        if not frame.empty:
            chunks.append(frame)
        chunk_start = chunk_end + pd.Timedelta(days=1)

    frame = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=sorted(REQUIRED_COLUMNS))
    frame = frame[
        (frame["date"] >= start) & (frame["date"] <= end)
    ].drop_duplicates(["symbol", "date"], keep="last").sort_values(["date", "variety", "symbol"]).reset_index(drop=True)
    if frame.empty:
        raise CffexSourceUnavailable(f"{endpoint} returned no IF/IC/IH rows for {start_date}..{end_date}")
    return CffexFetchResult(
        frame=frame,
        source_id="cffex_official",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note=f"AkShare get_futures_daily official CFFEX range data in {batch_days}-day batches; main contract is selected by daily volume, then open interest",
    )


def select_main_contracts(frame: pd.DataFrame) -> pd.DataFrame:
    """Select the dominant contract for each variety and trading day."""
    normalized = normalize_cffex_daily(frame)
    if normalized.empty:
        return normalized.assign(contract_label=pd.Series(dtype="string"))

    ranked = normalized.sort_values(
        ["date", "variety", "volume", "open_interest", "symbol"],
        ascending=[True, True, False, False, True],
    )
    selected = ranked.drop_duplicates(["date", "variety"], keep="first").copy()
    selected["contract_label"] = selected["variety"] + " main"
    return selected.sort_values(["date", "variety"]).reset_index(drop=True)


def main_contract_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return one wide close-price row per date for IF, IC and IH."""
    selected = select_main_contracts(frame)
    if selected.empty:
        return pd.DataFrame(columns=["date", "if_close", "ic_close", "ih_close"])
    wide = selected.pivot(index="date", columns="variety", values="close").reset_index()
    wide.columns.name = None
    for variety in VARIETIES:
        if variety not in wide.columns:
            wide[variety] = pd.NA
    return wide.rename(columns={"IF": "if_close", "IC": "ic_close", "IH": "ih_close"})[
        ["date", "if_close", "ic_close", "ih_close"]
    ].sort_values("date").reset_index(drop=True)


def dominant_contract_label(symbol: str) -> str:
    """Convert a contract code to a stable display label."""
    match = re.match(r"([A-Z]+)(\d+)$", str(symbol).upper().strip())
    return f"{match.group(1)} {match.group(2)}" if match else str(symbol)


def parse_index_futures_contract_month(symbol: str) -> pd.Timestamp:
    """Return the first day of the contract month for IF/IC/IH-style symbols."""
    match = re.match(r"^(IF|IC|IH)(\d{2})(\d{2})$", str(symbol).upper().strip())
    if not match:
        raise ValueError(f"Unsupported CFFEX index futures symbol: {symbol}")
    year = 2000 + int(match.group(2))
    month = int(match.group(3))
    return pd.Timestamp(year=year, month=month, day=1)


def third_friday(year: int, month: int) -> pd.Timestamp:
    """Return the third Friday in a month."""
    first = pd.Timestamp(year=year, month=month, day=1)
    days_until_friday = (4 - first.weekday()) % 7
    return first + pd.Timedelta(days=days_until_friday + 14)


def index_futures_expiry_date(
    symbol: str,
    trade_dates: Iterable[pd.Timestamp] | None = None,
) -> pd.Timestamp:
    """Return the stock-index-futures delivery date, adjusted to a trading day.

    CFFEX stock-index futures normally expire on the third Friday of the
    contract month. If that date is not present in the supplied mainland China
    trading calendar, use the latest trading day before it.
    """
    month_start = parse_index_futures_contract_month(symbol)
    expiry = third_friday(month_start.year, month_start.month).normalize()
    if trade_dates is None:
        return expiry
    dates = pd.DatetimeIndex(pd.to_datetime(list(trade_dates), errors="coerce")).dropna().normalize()
    candidates = dates[dates <= expiry]
    if candidates.empty:
        return expiry
    return pd.Timestamp(candidates.max()).normalize()


def days_to_expiry(
    trade_date: pd.Timestamp,
    symbol: str,
    trade_dates: Iterable[pd.Timestamp] | None = None,
) -> int:
    """Calendar days from a trading date to the contract expiry date."""
    expiry = index_futures_expiry_date(symbol, trade_dates)
    return int((expiry - pd.Timestamp(trade_date).normalize()).days)
