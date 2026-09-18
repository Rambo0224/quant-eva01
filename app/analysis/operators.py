from __future__ import annotations

import numpy as np
import pandas as pd


def divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """A zero denominator is invalid, never a zero/neutral signal."""
    return (numerator / denominator.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def smooth(values: pd.Series, alpha: float) -> pd.Series:
    """Seed with first valid observation; gaps don't consume recursive weight.

    Return NaN on a missing date, then resume using the last valid state.
    """
    return values.ewm(alpha=alpha, adjust=False, ignore_na=True).mean().where(values.notna())


def sma(values: pd.Series, n: int, m: int = 1) -> pd.Series:
    return smooth(values, m / n)


def ema(values: pd.Series, n: int) -> pd.Series:
    return smooth(values, 2 / (n + 1))


def percentile(values: pd.Series, n: int) -> pd.Series:
    # Midrank gives a constant series a neutral 50, not a false extreme.
    return values.rolling(n).apply(
        lambda x: 100 * ((x < x[-1]).sum() + 0.5 * (x == x[-1]).sum()) / len(x), raw=True
    )
