from __future__ import annotations

from math import sqrt
from typing import Iterable

import pandas as pd


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.where(denominator.ne(0))
    return numerator.divide(denominator)


def change_rate(series: pd.Series, periods: int = 1) -> pd.Series:
    return series.pct_change(periods=periods)


def annualized_volatility(close: pd.Series, window: int = 20, periods_per_year: int = 252) -> pd.Series:
    returns = close.pct_change()
    return returns.rolling(window).std() * sqrt(periods_per_year)


def basis(futures: pd.Series, spot: pd.Series) -> pd.Series:
    return futures - spot


def annualized_basis_rate(
    futures: pd.Series,
    spot: pd.Series,
    days_to_expiry: pd.Series,
    days_per_year: int = 365,
) -> pd.Series:
    raw_basis_rate = safe_ratio(futures, spot) - 1
    valid_days = days_to_expiry.where(days_to_expiry.gt(0))
    return raw_basis_rate * days_per_year / valid_days


def rolling_rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(window).mean()
    losses = -delta.clip(upper=0).rolling(window).mean()
    relative_strength = safe_ratio(gains, losses)
    return 100 - (100 / (1 + relative_strength))


def rolling_atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window).mean()


def cross_section_statistics(
    frame: pd.DataFrame,
    date_column: str = "date",
    close_column: str = "close",
    open_column: str = "open",
    high_column: str = "high",
    low_column: str = "low",
    volume_column: str = "volume",
    free_shares_column: str = "free_shares",
    turnover_rate_column: str = "turnover_rate",
) -> pd.DataFrame:
    """Calculate the daily cross-sectional metrics used by the indicator catalog.

    The input must contain one row per security and date, sorted by security/date.
    Missing fields are left absent; a metric is only emitted when its inputs exist.
    """
    required = {date_column, "code", close_column}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"cross-section frame missing columns: {sorted(missing)}")

    working = frame.copy()
    working[date_column] = pd.to_datetime(working[date_column], errors="coerce")
    working = working.dropna(subset=[date_column, "code", close_column]).sort_values(["code", date_column])
    grouped = working.groupby("code", group_keys=False)
    working["return_5d"] = grouped[close_column].pct_change(5)
    working["return_20d"] = grouped[close_column].pct_change(20)
    working["return_60d"] = grouped[close_column].pct_change(60)
    working["ma20"] = grouped[close_column].transform(lambda value: value.rolling(20).mean())
    working["ma60"] = grouped[close_column].transform(lambda value: value.rolling(60).mean())
    working["ma120"] = grouped[close_column].transform(lambda value: value.rolling(120).mean())
    working["high_60d"] = grouped[close_column].transform(lambda value: value.shift(1).rolling(60).max())
    working["high_120d"] = grouped[close_column].transform(lambda value: value.shift(1).rolling(120).max())
    working["high_250d"] = grouped[close_column].transform(lambda value: value.shift(1).rolling(250).max())
    working["volume_ma20"] = (
        grouped[volume_column].transform(lambda value: value.rolling(20).mean())
        if volume_column in working.columns
        else pd.NA
    )

    if turnover_rate_column in working.columns:
        working["turnover_individual"] = pd.to_numeric(working[turnover_rate_column], errors="coerce") / 100.0
    elif volume_column in working.columns and free_shares_column in working.columns:
        working["turnover_individual"] = safe_ratio(working[volume_column], working[free_shares_column])
    if volume_column in working.columns:
        working["volume_ratio_20d"] = safe_ratio(working[volume_column], working["volume_ma20"])
    working["bias_20d"] = safe_ratio(working[close_column] - working["ma20"], working["ma20"])
    if open_column in working.columns:
        working["advance_flag"] = working[close_column] > working[open_column]
    working["above_ma20_flag"] = working[close_column] > working["ma20"]
    working["above_ma60_flag"] = working[close_column] > working["ma60"]
    working["above_ma120_flag"] = working[close_column] > working["ma120"]
    working["high_60d_flag"] = working[close_column] >= working["high_60d"]
    working["high_120d_flag"] = working[close_column] >= working["high_120d"]
    working["high_250d_flag"] = working[close_column] >= working["high_250d"]
    if high_column in working.columns and low_column in working.columns:
        previous_close = grouped[close_column].shift(1)
        true_range = pd.concat(
            [
                working[high_column] - working[low_column],
                (working[high_column] - previous_close).abs(),
                (working[low_column] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        working["atr_14d"] = true_range.groupby(working["code"]).transform(lambda value: value.rolling(14).mean())
    working["rsi_14d"] = grouped[close_column].transform(lambda value: rolling_rsi(value, 14))

    numeric_metrics = {
        "return_20d": ["momentum_20d_median"],
        "return_60d": ["momentum_60d_median"],
        "return_5d": ["reversal_5d_median"],
        "turnover_individual": ["turnover_individual_median"],
        "volume_ratio_20d": ["volume_ratio_20d_median"],
        "bias_20d": ["bias_20d_median"],
        "atr_14d": ["atr_14d_median"],
        "rsi_14d": ["rsi_14d_median"],
    }
    flag_metrics = {
        "advance_flag": "advance_ratio_20d",
        "high_60d_flag": "high_60d_ratio",
        "high_120d_flag": "high_120d_ratio",
        "high_250d_flag": "high_250d_ratio",
        "above_ma20_flag": "above_ma20_ratio",
        "above_ma60_flag": "above_ma60_ratio",
        "above_ma120_flag": "above_ma120_ratio",
    }

    output: dict[str, pd.Series] = {}
    for source, names in numeric_metrics.items():
        if source not in working.columns:
            continue
        grouped_values = working.groupby(date_column)[source]
        for name in names:
            if name.endswith("_p10"):
                output[name] = grouped_values.quantile(0.10)
            elif name.endswith("_p90"):
                output[name] = grouped_values.quantile(0.90)
            else:
                output[name] = grouped_values.median()
    for source, name in flag_metrics.items():
        if source in working.columns:
            daily_ratio = working.groupby(date_column)[source].mean()
            output[name] = daily_ratio.rolling(20).mean()

    if not output:
        return pd.DataFrame(index=pd.Index([], name=date_column))
    result = pd.DataFrame(output).sort_index()
    result.index.name = date_column
    return result.reset_index()
