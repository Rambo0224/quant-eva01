"""Standardized-data-first planning for daily market-data updates."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .storage import ROOT


@dataclass(frozen=True)
class MarketUpdatePlan:
    """The material work required to make one standardized series current."""

    kind: str
    symbol: str
    target_date: str
    standardized_latest_date: str | None
    standardized_missing_dates: tuple[str, ...]
    raw_ready_dates: tuple[str, ...]
    fetch_windows: tuple[tuple[str, str], ...]

    @property
    def status(self) -> str:
        if not self.standardized_missing_dates:
            return "standardized_current"
        if not self.fetch_windows:
            return "raw_ready_for_standardization"
        return "raw_data_missing"


def _table_exists(conn, schema: str, table: str) -> bool:
    return bool(conn.execute("""SELECT 1 FROM information_schema.tables
        WHERE table_schema=? AND table_name=?""", [schema, table]).fetchone())


def canonical_symbol(symbol: str) -> str:
    code = str(symbol)[-6:]
    exchange = "SH" if code.startswith(("5", "6")) else "BJ" if code.startswith(("4", "8", "9")) else "SZ"
    return f"{code}.{exchange}"


def official_trading_dates(start: str, end: str) -> tuple[pd.Timestamp, ...]:
    """Read the maintained official calendar; use the explicit target as fallback."""
    path = ROOT / "data" / "baostock_trade_dates.csv"
    if path.exists():
        calendar = pd.read_csv(path)
        if {"calendar_date", "is_trading_day"}.issubset(calendar.columns):
            dates = pd.to_datetime(calendar.loc[calendar.is_trading_day.astype(str).isin(["1", "1.0"]), "calendar_date"])
            dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
            if not dates.empty:
                return tuple(dates.sort_values().drop_duplicates())
    return (pd.Timestamp(end),)


def _standardized_latest_date(conn, kind: str, symbol: str, adjustment: str, factor: str | None):
    if kind == "index":
        indicator = factor.replace("_", "-")
        series = f"{indicator}:{factor}"
        if not _table_exists(conn, "core", "observations"):
            return None
        return conn.execute("SELECT max(obs_time)::DATE FROM core.observations WHERE indicator_id=? AND series_id=?",
                            [indicator, series]).fetchone()[0]

    if not _table_exists(conn, "core", "market_daily_bars"):
        return None
    return conn.execute("""SELECT max(trade_date)::DATE FROM core.market_daily_bars
        WHERE symbol=? AND price_adjustment=?""", [canonical_symbol(symbol), adjustment]).fetchone()[0]


def _raw_dates(conn, kind: str, symbol: str, start: str, end: str, adjustment: str, factor: str | None) -> set:
    if kind == "index":
        indicator = factor.replace("_", "-")
        series = f"{indicator}:{factor}"
        return {row[0] for row in conn.execute("""SELECT obs_time::DATE FROM observations
            WHERE indicator_id=? AND series_id=? AND obs_time BETWEEN ?::DATE AND ?::DATE""",
            [indicator, series, start, end]).fetchall()}
    return {row[0] for row in conn.execute("""SELECT obs_time::DATE FROM equity_daily_bars
        WHERE symbol=? AND obs_time BETWEEN ?::DATE AND ?::DATE
        AND json_extract_string(extra,'$.price_adjustment')=?""", [symbol, start, end, adjustment]).fetchall()}


def _contiguous_windows(missing: set, expected: tuple[pd.Timestamp, ...]) -> tuple[tuple[str, str], ...]:
    positions = {day.date(): index for index, day in enumerate(expected)}
    windows: list[list] = []
    for day in sorted(missing):
        if not windows or positions[day] != positions[windows[-1][1]] + 1:
            windows.append([day, day])
        else:
            windows[-1][1] = day
    return tuple((left.isoformat(), right.isoformat()) for left, right in windows)


def plan_market_update(conn, kind: str, symbol: str, target_date: str, configured_start: str, adjustment: str, factor: str | None = None) -> MarketUpdatePlan:
    """Plan only from the latest standardized date through the requested date."""
    latest = _standardized_latest_date(conn, kind, symbol, adjustment, factor)
    target = pd.Timestamp(target_date).date()
    if latest and latest >= target:
        return MarketUpdatePlan(kind, symbol, target_date, latest.isoformat(), (), (), ())
    start = configured_start if latest is None else (pd.Timestamp(latest) + pd.Timedelta(days=1)).date().isoformat()
    expected = official_trading_dates(start, target_date)
    expected_dates = {day.date() for day in expected}
    raw = _raw_dates(conn, kind, symbol, start, target_date, adjustment, factor)
    raw_ready = expected_dates & raw
    source_missing = expected_dates - raw
    return MarketUpdatePlan(
        kind=kind,
        symbol=symbol,
        target_date=target_date,
        standardized_latest_date=latest.isoformat() if latest else None,
        standardized_missing_dates=tuple(day.isoformat() for day in sorted(expected_dates)),
        raw_ready_dates=tuple(day.isoformat() for day in sorted(raw_ready)),
        fetch_windows=_contiguous_windows(source_missing, expected),
    )
