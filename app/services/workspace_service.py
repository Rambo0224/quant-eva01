from __future__ import annotations

from dataclasses import asdict

from datahub.registry.loader import get_workspace_definition, load_workspace_definitions, refresh_registry_if_needed


def list_workspaces() -> list[dict]:
    refresh_registry_if_needed()
    return [asdict(item) for item in load_workspace_definitions()]


def get_workspace(workspace_id: str) -> dict:
    refresh_registry_if_needed()
    return asdict(get_workspace_definition(workspace_id))
