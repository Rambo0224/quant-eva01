from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from macro_replay.config import INDICATORS_FILE, MODULES_DIR, load_indicators, load_themes

from .models import DatasetDefinition, ViewDefinition, WorkspaceDefinition

CONFIG_DIR = Path("config")
VIEWS_DIR = CONFIG_DIR / "views"
WORKSPACES_DIR = CONFIG_DIR / "workspaces"
_REGISTRY_SIGNATURE: tuple[tuple[str, int, int], ...] | None = None


def _path_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return (str(path), -1, -1)


def _registry_signature() -> tuple[tuple[str, int, int], ...]:
    signatures = [_path_signature(INDICATORS_FILE)]
    if MODULES_DIR.exists():
        signatures.extend(_path_signature(path) for path in sorted(MODULES_DIR.glob("*.yaml")))
    if VIEWS_DIR.exists():
        signatures.extend(_path_signature(path) for path in sorted(VIEWS_DIR.glob("*.yaml")))
    if WORKSPACES_DIR.exists():
        signatures.extend(_path_signature(path) for path in sorted(WORKSPACES_DIR.glob("*.yaml")))
    return tuple(signatures)


def refresh_registry_if_needed() -> None:
    global _REGISTRY_SIGNATURE

    signature = _registry_signature()
    if _REGISTRY_SIGNATURE is None:
        _REGISTRY_SIGNATURE = signature
        return
    if signature != _REGISTRY_SIGNATURE:
        clear_registry_caches()
        _REGISTRY_SIGNATURE = signature


def clear_registry_caches() -> None:
    load_dataset_definitions.cache_clear()
    load_view_definitions.cache_clear()
    load_workspace_definitions.cache_clear()


def _supports_compare(chart_type: str) -> bool:
    return chart_type == "line"


def _read_registry_objects(directory: Path, top_level_key: str) -> list[dict[str, Any]]:
    if not directory.exists():
        return []

    records: list[dict[str, Any]] = []
    for file_path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, list):
            items = raw
        else:
            items = raw.get(top_level_key, [])
        if not isinstance(items, list):
            raise ValueError(f"{file_path} should define a list under '{top_level_key}'")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{file_path} contains a non-object entry under '{top_level_key}'")
            records.append(item)
    return records


@lru_cache(maxsize=1)
def load_dataset_definitions() -> list[DatasetDefinition]:
    theme_titles = {theme.key: theme.title for theme in load_themes()}
    datasets: list[DatasetDefinition] = []
    for indicator in load_indicators():
        chart_cfg = indicator.chart or {}
        y_field = chart_cfg.get("y_field", "value")
        series_codes = [item.get("code", "") for item in (indicator.series or []) if item.get("code")]
        tags = [indicator.theme, indicator.source, *series_codes]
        datasets.append(
            DatasetDefinition(
                id=indicator.id,
                title=indicator.title,
                description=indicator.description,
                workspace_id=indicator.theme,
                workspace_title=theme_titles.get(indicator.theme, indicator.theme),
                source=indicator.source,
                chart_type=chart_cfg.get("type", "line"),
                y_field=y_field,
                x_field=chart_cfg.get("x_field", "date"),
                unit=chart_cfg.get("unit", chart_cfg.get("y_label", "")),
                tags=[tag for tag in tags if tag],
                series_codes=series_codes,
                supports_compare=_supports_compare(chart_cfg.get("type", "line")),
            )
        )
    return datasets


def _default_view_definitions() -> list[ViewDefinition]:
    views: list[ViewDefinition] = []
    for dataset in load_dataset_definitions():
        views.append(
            ViewDefinition(
                id=f"{dataset.id}.default",
                title=dataset.title,
                dataset_id=dataset.id,
                workspace_id=dataset.workspace_id,
                description=dataset.description,
                chart_type=dataset.chart_type,
                x_field=dataset.x_field,
                y_field=dataset.y_field,
                supports_compare=dataset.supports_compare,
                default_transform="identity",
                default_rolling_window=1,
                note="",
            )
        )
    return views


@lru_cache(maxsize=1)
def load_view_definitions() -> list[ViewDefinition]:
    dataset_by_id = {dataset.id: dataset for dataset in load_dataset_definitions()}
    view_by_id = {view.id: view for view in _default_view_definitions()}

    for item in _read_registry_objects(VIEWS_DIR, "views"):
        dataset_id = item["dataset_id"]
        dataset = dataset_by_id.get(dataset_id)
        if dataset is None:
            raise KeyError(f"Unknown dataset in view config: {dataset_id}")

        view_id = item["id"]
        base = view_by_id.get(view_id)
        view_by_id[view_id] = ViewDefinition(
            id=view_id,
            title=item.get("title", base.title if base else dataset.title),
            dataset_id=dataset_id,
            workspace_id=item.get("workspace_id", base.workspace_id if base else dataset.workspace_id),
            description=item.get("description", base.description if base else dataset.description),
            chart_type=item.get("chart_type", base.chart_type if base else dataset.chart_type),
            x_field=item.get("x_field", base.x_field if base else dataset.x_field),
            y_field=item.get("y_field", base.y_field if base else dataset.y_field),
            supports_compare=bool(item.get("supports_compare", base.supports_compare if base else dataset.supports_compare)),
            default_transform=item.get("default_transform", base.default_transform if base else "identity"),
            default_rolling_window=int(item.get("default_rolling_window", base.default_rolling_window if base else 1)),
            note=item.get("note", base.note if base else ""),
        )

    return list(view_by_id.values())


def _default_workspace_definitions() -> list[WorkspaceDefinition]:
    datasets = load_dataset_definitions()
    views = load_view_definitions()
    dataset_by_workspace: dict[str, list[str]] = {}
    view_by_workspace: dict[str, list[str]] = {}
    workspace_meta: dict[str, tuple[str, str]] = {}
    for dataset in datasets:
        dataset_by_workspace.setdefault(dataset.workspace_id, []).append(dataset.id)
        workspace_meta.setdefault(dataset.workspace_id, (dataset.workspace_title, ""))
    for view in views:
        view_by_workspace.setdefault(view.workspace_id, []).append(view.id)

    workspaces: list[WorkspaceDefinition] = []
    for workspace_id, dataset_ids in dataset_by_workspace.items():
        title, description = workspace_meta.get(workspace_id, (workspace_id, ""))
        workspaces.append(
            WorkspaceDefinition(
                id=workspace_id,
                title=title,
                description=description,
                dataset_ids=dataset_ids,
                view_ids=view_by_workspace.get(workspace_id, []),
                focus_dataset_ids=dataset_ids[: min(4, len(dataset_ids))],
                headline="",
                research_questions=[],
            )
        )
    return workspaces


@lru_cache(maxsize=1)
def load_workspace_definitions() -> list[WorkspaceDefinition]:
    valid_dataset_ids = {dataset.id for dataset in load_dataset_definitions()}
    valid_view_ids = {view.id for view in load_view_definitions()}
    workspace_by_id = {workspace.id: workspace for workspace in _default_workspace_definitions()}

    for item in _read_registry_objects(WORKSPACES_DIR, "workspaces"):
        workspace_id = item["id"]
        base = workspace_by_id.get(workspace_id)

        configured_dataset_ids = item.get("dataset_ids")
        if configured_dataset_ids is None and base is not None:
            dataset_ids = list(base.dataset_ids)
        else:
            dataset_ids = list(configured_dataset_ids or [])

        configured_view_ids = item.get("view_ids")
        if configured_view_ids is None and base is not None:
            view_ids = list(base.view_ids)
        else:
            view_ids = list(configured_view_ids or [])

        unknown_datasets = sorted(dataset_id for dataset_id in dataset_ids if dataset_id not in valid_dataset_ids)
        if unknown_datasets:
            raise KeyError(f"Unknown datasets in workspace config '{workspace_id}': {', '.join(unknown_datasets)}")

        focus_dataset_ids = list(item.get("focus_dataset_ids", base.focus_dataset_ids if base else dataset_ids[: min(4, len(dataset_ids))]))
        unknown_focus_datasets = sorted(dataset_id for dataset_id in focus_dataset_ids if dataset_id not in valid_dataset_ids)
        if unknown_focus_datasets:
            raise KeyError(
                f"Unknown focus datasets in workspace config '{workspace_id}': {', '.join(unknown_focus_datasets)}"
            )

        unknown_views = sorted(view_id for view_id in view_ids if view_id not in valid_view_ids)
        if unknown_views:
            raise KeyError(f"Unknown views in workspace config '{workspace_id}': {', '.join(unknown_views)}")

        workspace_by_id[workspace_id] = WorkspaceDefinition(
            id=workspace_id,
            title=item.get("title", base.title if base else workspace_id),
            description=item.get("description", base.description if base else ""),
            dataset_ids=dataset_ids,
            view_ids=view_ids,
            focus_dataset_ids=focus_dataset_ids,
            headline=item.get("headline", base.headline if base else ""),
            research_questions=list(item.get("research_questions", base.research_questions if base else [])),
        )

    return list(workspace_by_id.values())


def get_dataset_definition(dataset_id: str) -> DatasetDefinition:
    for dataset in load_dataset_definitions():
        if dataset.id == dataset_id:
            return dataset
    raise KeyError(f"Unknown dataset: {dataset_id}")


def get_view_definition(view_id: str) -> ViewDefinition:
    for view in load_view_definitions():
        if view.id == view_id:
            return view
    raise KeyError(f"Unknown view: {view_id}")


def get_workspace_definition(workspace_id: str) -> WorkspaceDefinition:
    for workspace in load_workspace_definitions():
        if workspace.id == workspace_id:
            return workspace
    raise KeyError(f"Unknown workspace: {workspace_id}")
