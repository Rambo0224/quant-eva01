from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DatasetDefinition:
    id: str
    title: str
    description: str
    workspace_id: str
    workspace_title: str
    source: str
    chart_type: str
    y_field: str
    x_field: str
    unit: str
    tags: list[str] = field(default_factory=list)
    series_codes: list[str] = field(default_factory=list)
    supports_compare: bool = True


@dataclass(frozen=True)
class ViewDefinition:
    id: str
    title: str
    dataset_id: str
    workspace_id: str
    description: str
    chart_type: str
    x_field: str
    y_field: str
    supports_compare: bool = True
    default_transform: str = "identity"
    default_rolling_window: int = 1
    note: str = ""


@dataclass(frozen=True)
class WorkspaceDefinition:
    id: str
    title: str
    description: str
    dataset_ids: list[str] = field(default_factory=list)
    view_ids: list[str] = field(default_factory=list)
    focus_dataset_ids: list[str] = field(default_factory=list)
    headline: str = ""
    research_questions: list[str] = field(default_factory=list)
