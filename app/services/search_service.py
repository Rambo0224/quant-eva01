from __future__ import annotations

from typing import Any

from .dataset_service import list_datasets
from .view_service import list_views
from .workspace_service import list_workspaces


def _matches(query: str, *parts: str) -> bool:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return True
    haystack = " ".join(part for part in parts if part).lower()
    return normalized_query in haystack


def search_registry(query: str | None = None, workspace_id: str | None = None) -> dict[str, Any]:
    normalized_query = (query or "").strip()
    datasets = list_datasets(query=normalized_query or None, workspace_id=workspace_id)
    workspace_index = {workspace["id"]: workspace for workspace in list_workspaces()}
    dataset_index = {dataset["id"]: dataset for dataset in list_datasets(workspace_id=workspace_id)}

    views: list[dict[str, Any]] = []
    for view in list_views(workspace_id=workspace_id):
        dataset = dataset_index.get(view["dataset_id"])
        workspace = workspace_index.get(view["workspace_id"], {})
        if not _matches(
            normalized_query,
            view["id"],
            view["title"],
            view["description"],
            view["dataset_id"],
            dataset["title"] if dataset else "",
            workspace.get("title", ""),
        ):
            continue
        views.append(
            {
                **view,
                "dataset_title": dataset["title"] if dataset else view["dataset_id"],
                "workspace_title": workspace.get("title", view["workspace_id"]),
            }
        )

    workspaces: list[dict[str, Any]] = []
    for workspace in workspace_index.values():
        if workspace_id and workspace["id"] != workspace_id:
            continue
        if not _matches(
            normalized_query,
            workspace["id"],
            workspace["title"],
            workspace["description"],
            " ".join(workspace.get("dataset_ids", [])),
            " ".join(workspace.get("view_ids", [])),
        ):
            continue
        workspaces.append(workspace)

    return {
        "query": normalized_query,
        "datasets": datasets,
        "views": views,
        "workspaces": workspaces,
        "total": len(datasets) + len(views) + len(workspaces),
    }
