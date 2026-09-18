from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd

from datahub.registry.loader import get_view_definition, load_view_definitions, refresh_registry_if_needed
from datahub.transforms.common import apply_transform, merge_named_frames
from macro_replay.dashboard_service import get_indicator

from .dataset_service import get_dataset_series


def list_views(workspace_id: str | None = None) -> list[dict[str, Any]]:
    refresh_registry_if_needed()
    records: list[dict[str, Any]] = []
    for view in load_view_definitions():
        if workspace_id and view.workspace_id != workspace_id:
            continue
        records.append(asdict(view))
    return records


def get_view(view_id: str) -> dict[str, Any]:
    refresh_registry_if_needed()
    return asdict(get_view_definition(view_id))


def build_view_dataset(
    view_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    transform_name: str = "identity",
    rolling_window: int = 1,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    refresh_registry_if_needed()
    view = get_view_definition(view_id)
    dataset, plot_df, raw_df = get_dataset_series(view.dataset_id, start_date=start_date, end_date=end_date)
    transformed = apply_transform(
        plot_df,
        transform_name=transform_name,
        y_field=view.y_field,
        rolling_window=rolling_window,
        supports_compare=view.supports_compare,
    )
    payload = asdict(view)
    payload["dataset"] = dataset
    payload["indicator"] = get_indicator(view.dataset_id)
    return payload, transformed, raw_df


def build_comparison_dataset(
    dataset_ids: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    transform_name: str = "rebase",
    rolling_window: int = 1,
) -> pd.DataFrame:
    refresh_registry_if_needed()
    named_frames: dict[str, pd.DataFrame] = {}
    for dataset_id in dataset_ids:
        _, plot_df, _ = get_dataset_series(dataset_id, start_date=start_date, end_date=end_date)
        if plot_df.empty or "date" not in plot_df.columns:
            continue
        value_columns = [column for column in plot_df.columns if column != "date"]
        if not value_columns:
            continue
        target_field = value_columns[0]
        series_frame = plot_df[["date", target_field]].dropna().rename(columns={target_field: dataset_id})
        named_frames[dataset_id] = series_frame

    merged = merge_named_frames(named_frames)
    if merged.empty:
        return merged
    return apply_transform(
        merged,
        transform_name=transform_name,
        y_field=None,
        rolling_window=rolling_window,
        supports_compare=True,
    )
