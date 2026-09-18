from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd

from datahub.registry.loader import get_dataset_definition, load_dataset_definitions
from datahub.registry.loader import load_workspace_definitions, refresh_registry_if_needed
from macro_replay.dashboard_service import build_indicator_dataset, load_dashboard_cards
from macro_replay.source_labels import display_source_label


def _card_index() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in load_dashboard_cards()}


def _workspace_title_index() -> dict[str, str]:
    refresh_registry_if_needed()
    return {workspace.id: workspace.title for workspace in load_workspace_definitions()}


def list_datasets(query: str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
    refresh_registry_if_needed()
    normalized_query = (query or "").strip().lower()
    workspace_filter = workspace_id or None
    cards = _card_index()
    workspace_titles = _workspace_title_index()
    records: list[dict[str, Any]] = []
    for dataset in load_dataset_definitions():
        if workspace_filter and dataset.workspace_id != workspace_filter:
            continue
        haystack = " ".join(
            [
                dataset.id,
                dataset.title,
                dataset.description,
                dataset.workspace_id,
                " ".join(dataset.tags),
                " ".join(dataset.series_codes),
            ]
        ).lower()
        if normalized_query and normalized_query not in haystack:
            continue
        card = cards.get(dataset.id, {})
        records.append(
            {
                **asdict(dataset),
                "workspace_title": workspace_titles.get(dataset.workspace_id, dataset.workspace_title),
                "source_label": display_source_label(card.get("source") or dataset.source),
                "latest_value": card.get("latest_value"),
                "latest_date": card.get("latest_date"),
                "updated_at": card.get("updated_at"),
                "image_path": card.get("image_path"),
                "html_path": card.get("html_path"),
            }
        )
    return records


def get_dataset(dataset_id: str) -> dict[str, Any]:
    refresh_registry_if_needed()
    dataset = get_dataset_definition(dataset_id)
    card = _card_index().get(dataset_id, {})
    workspace_titles = _workspace_title_index()
    payload = asdict(dataset)
    payload.update(
        {
            "workspace_title": workspace_titles.get(dataset.workspace_id, dataset.workspace_title),
            "source_label": display_source_label(card.get("source") or dataset.source),
            "latest_value": card.get("latest_value"),
            "latest_date": card.get("latest_date"),
            "updated_at": card.get("updated_at"),
            "image_path": card.get("image_path"),
            "html_path": card.get("html_path"),
        }
    )
    return payload


def get_dataset_series(dataset_id: str, start_date: str | None = None, end_date: str | None = None) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    refresh_registry_if_needed()
    indicator, plot_df, raw_df = build_indicator_dataset(dataset_id, start_date=start_date, end_date=end_date)
    payload = get_dataset(dataset_id)
    payload["indicator"] = indicator
    return payload, plot_df, raw_df
