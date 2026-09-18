from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Dict, List

import pandas as pd


@dataclass
class FredSeries:
    code: str
    url: str


def fetch_series(series: FredSeries) -> pd.DataFrame:
    df = pd.read_csv(series.url)
    df.columns = ["date", series.code]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df[series.code] = pd.to_numeric(df[series.code], errors="coerce")
    return df


def fetch_breakeven(series_defs: List[Dict[str, str]], start_date: str) -> pd.DataFrame:
    frames = [fetch_series(FredSeries(**cfg)) for cfg in series_defs]
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="date", how="inner")
    merged = merged.dropna()
    merged = merged[merged["date"] >= pd.Timestamp(start_date)]
    merged["breakeven"] = merged.iloc[:, 1] - merged.iloc[:, 2]
    return merged
