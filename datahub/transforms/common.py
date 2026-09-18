from __future__ import annotations

from typing import Any

import pandas as pd


TRANSFORM_LABELS: dict[str, str] = {
    "identity": "原始值",
    "rebase": "基期归一(=100)",
    "pct_change": "日变化率(%)",
    "zscore": "标准分(Z-Score)",
}


def merge_named_frames(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    merged: pd.DataFrame | None = None
    for frame in frames.values():
        if merged is None:
            merged = frame.copy()
        else:
            merged = merged.merge(frame, on="date", how="outer")
    if merged is None:
        return pd.DataFrame()
    return merged.sort_values("date").reset_index(drop=True)


def _apply_rolling(frame: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    if rolling_window <= 1:
        return frame
    result = frame.copy()
    value_columns = [column for column in result.columns if column != "date"]
    result[value_columns] = result[value_columns].rolling(window=rolling_window, min_periods=1).mean()
    return result


def _identity(frame: pd.DataFrame) -> pd.DataFrame:
    return frame


def _rebase(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in [item for item in result.columns if item != "date"]:
        valid = result[column].dropna()
        if valid.empty:
            continue
        base = valid.iloc[0]
        if base == 0:
            continue
        result[column] = result[column] / base * 100.0
    return result


def _pct_change(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    value_columns = [column for column in result.columns if column != "date"]
    result[value_columns] = result[value_columns].pct_change() * 100.0
    return result.dropna(how="all", subset=value_columns).reset_index(drop=True)


def _zscore(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in [item for item in result.columns if item != "date"]:
        series = result[column]
        std = series.std()
        if std is None or std == 0 or pd.isna(std):
            continue
        result[column] = (series - series.mean()) / std
    return result


def apply_transform(
    frame: pd.DataFrame,
    transform_name: str,
    y_field: str | None,
    rolling_window: int,
    supports_compare: bool,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.copy()
    if y_field and y_field in result.columns and supports_compare:
        result = result[["date", y_field]].dropna().reset_index(drop=True)
    elif "date" in result.columns:
        value_columns = [column for column in result.columns if column != "date"]
        result = result[["date", *value_columns]].dropna(how="all", subset=value_columns).reset_index(drop=True)

    if transform_name == "rebase":
        result = _rebase(result)
    elif transform_name == "pct_change":
        result = _pct_change(result)
    elif transform_name == "zscore":
        result = _zscore(result)
    else:
        result = _identity(result)

    return _apply_rolling(result, rolling_window)
