from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.dataset_service import get_dataset, get_dataset_series, list_datasets
from app.services.search_service import search_registry
from app.services.status_service import load_latest_manual_imports
from app.services.view_service import build_comparison_dataset, build_view_dataset, list_views
from app.services.workspace_service import get_workspace, list_workspaces
from datahub.transforms.common import TRANSFORM_LABELS
from macro_replay.charts import build_chart_figure


def _apply_terminal_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --paper: #f5efe2;
            --ink: #132238;
            --muted: #59667a;
            --panel: rgba(255, 252, 246, 0.88);
            --border: rgba(19, 34, 56, 0.12);
            --accent: #b55f2b;
            --accent-soft: #e7c9b6;
            --sea: #1f4e5f;
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(181, 95, 43, 0.18), transparent 28%),
                radial-gradient(circle at top right, rgba(31, 78, 95, 0.16), transparent 26%),
                linear-gradient(180deg, #f8f3ea 0%, #f0e8d8 100%);
            color: var(--ink);
        }
        .block-container {
            max-width: 1680px;
            padding-top: 0.45rem;
            padding-bottom: 1rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }
        [data-testid="stSidebar"] {
            background: rgba(255, 249, 240, 0.94);
            border-right: 1px solid var(--border);
        }
        .hero-shell {
            padding: 0.4rem 0.55rem 0.42rem 0.55rem;
            border: 1px solid var(--border);
            border-radius: 14px;
            background: rgba(255,255,255,0.78);
            box-shadow: 0 8px 18px rgba(58, 45, 33, 0.04);
            margin-bottom: 0.45rem;
        }
        .metric-strip {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 0.35rem;
            margin: 0;
        }
        .metric-card {
            background: rgba(255,255,255,0.7);
            border: 1px solid rgba(19, 34, 56, 0.08);
            border-radius: 10px;
            padding: 0.42rem 0.5rem;
        }
        .metric-label {
            font-size: 0.62rem;
            color: var(--muted);
            margin-bottom: 0.08rem;
        }
        .metric-value {
            font-size: 0.88rem;
            font-weight: 700;
            color: var(--ink);
        }
        .section-shell {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 0.72rem 0.8rem 0.65rem 0.8rem;
            box-shadow: 0 10px 24px rgba(58, 45, 33, 0.045);
        }
        .section-title {
            font-size: 0.98rem;
            font-weight: 800;
            color: var(--ink);
            margin-bottom: 0.08rem;
        }
        .section-copy {
            color: var(--muted);
            font-size: 0.8rem;
            margin-bottom: 0.45rem;
        }
        .dataset-card {
            border: 1px solid rgba(19, 34, 56, 0.12);
            border-radius: 16px;
            padding: 0.62rem 0.68rem 0.42rem 0.68rem;
            background: rgba(255,255,255,0.84);
            min-height: 100%;
        }
        .dataset-title {
            font-size: 0.76rem;
            font-weight: 700;
            color: var(--ink);
            line-height: 1.15;
            margin-bottom: 0.12rem;
            white-space: nowrap;
        }
        .action-row {
            display: flex;
            align-items: center;
            gap: 0.45rem;
            margin-top: 0.1rem;
            margin-bottom: 0.05rem;
            flex-wrap: wrap;
        }
        .action-link {
            display: inline;
            padding: 0;
            border: none;
            background: transparent;
            color: var(--sea);
            font-size: 0.72rem;
            font-weight: 600;
            text-decoration: none;
            line-height: 1.2;
            transition: color 120ms ease;
        }
        .action-link:hover {
            color: var(--accent);
            text-decoration: underline;
        }
        .back-link {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0.55rem 0.85rem;
            border-radius: 999px;
            border: 1px solid rgba(19, 34, 56, 0.12);
            color: var(--ink);
            text-decoration: none;
            font-weight: 700;
            background: rgba(255,255,255,0.9);
            margin-bottom: 0.9rem;
        }
        .detail-shell {
            background: rgba(255,255,255,0.9);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 0.82rem 0.9rem 0.72rem 0.9rem;
            box-shadow: 0 10px 24px rgba(58, 45, 33, 0.055);
        }
        .detail-title {
            font-size: 1.35rem;
            font-weight: 800;
            color: var(--ink);
            margin-bottom: 0.1rem;
        }
        .detail-meta {
            color: var(--muted);
            font-size: 0.78rem;
            margin-bottom: 0.45rem;
        }
        .catalog-hint {
            color: var(--muted);
            font-size: 0.78rem;
            margin-bottom: 0.3rem;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: rgba(255, 252, 246, 0.88);
            border: 1px solid rgba(19, 34, 56, 0.12);
            border-radius: 18px;
            box-shadow: 0 10px 24px rgba(58, 45, 33, 0.05);
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div {
            padding: 0.72rem 0.8rem 0.68rem 0.8rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _build_excel_bytes(raw_df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    export_df = raw_df.copy()
    if "extra" in export_df.columns:
        export_df["extra"] = export_df["extra"].apply(lambda value: "" if value in ({}, None) else str(value))
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="raw_data")
    buffer.seek(0)
    return buffer.getvalue()


def _format_value(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


def _build_panel_url(
    panel: str,
    dataset_id: str,
    start_date: str,
    end_date: str,
    transform_name: str,
    rolling_window: int,
) -> str:
    return (
        f"?panel={panel}"
        f"&dataset={dataset_id}"
        f"&start={start_date}"
        f"&end={end_date}"
        f"&transform={transform_name}"
        f"&window={rolling_window}"
    )


def _resolve_image_path(dataset: dict) -> Path | None:
    image_path = dataset.get("image_path")
    if not image_path:
        return None
    path = PROJECT_ROOT / image_path
    if not path.exists():
        return None
    return path


def _render_action_links(
    dataset_id: str,
    start_date: str,
    end_date: str,
    transform_name: str,
    rolling_window: int,
) -> None:
    chart_url = _build_panel_url("chart", dataset_id, start_date, end_date, transform_name, rolling_window)
    data_url = _build_panel_url("data", dataset_id, start_date, end_date, transform_name, rolling_window)
    st.markdown(
        (
            '<div class="action-row">'
            f'<a class="action-link" href="{chart_url}" target="_blank" rel="noopener noreferrer">交互图</a>'
            f'<a class="action-link" href="{data_url}" target="_blank" rel="noopener noreferrer">原始数据</a>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_dataset_chart_page(
    dataset_id: str,
    start_date: str,
    end_date: str,
    transform_name: str,
    rolling_window: int,
) -> None:
    dataset = get_dataset(dataset_id)
    view_id = f"{dataset_id}.default"
    view_payload, plot_df, _ = build_view_dataset(
        view_id,
        start_date=start_date,
        end_date=end_date,
        transform_name=transform_name if dataset["supports_compare"] else "identity",
        rolling_window=rolling_window,
    )
    indicator = view_payload["indicator"]
    st.markdown('<a class="back-link" href="./">返回终端</a>', unsafe_allow_html=True)
    st.markdown(f'<div class="detail-title">{dataset["title"]}</div>', unsafe_allow_html=True)
    st.markdown(
        (
            '<div class="detail-meta">'
            f'数据集: {dataset["id"]} | 时间范围: {start_date} 至 {end_date}'
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    if plot_df.empty:
        st.info("当前时间范围内没有图表数据。")
        return
    figure = build_chart_figure(plot_df, indicator, compact=False)
    st.plotly_chart(figure, width="stretch", key=f"standalone-chart-{dataset_id}-{start_date}-{end_date}")


def _render_dataset_data_page(dataset_id: str, start_date: str, end_date: str) -> None:
    dataset, _, raw_df = get_dataset_series(dataset_id, start_date=start_date, end_date=end_date)
    st.markdown('<a class="back-link" href="./">返回终端</a>', unsafe_allow_html=True)
    st.markdown(f'<div class="detail-title">{dataset["title"]} 原始数据</div>', unsafe_allow_html=True)
    st.markdown(
        (
            '<div class="detail-meta">'
            f'数据集: {dataset["id"]} | 时间范围: {start_date} 至 {end_date}'
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    st.dataframe(raw_df, width="stretch", hide_index=True)
    st.download_button(
        "下载 Excel",
        data=_build_excel_bytes(raw_df),
        file_name=f"{dataset_id}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"download-standalone-{dataset_id}-{start_date}-{end_date}",
        width="stretch",
    )


def _render_hero(workspace: dict, dataset_count: int, compare_candidates: int) -> None:
    st.markdown(
        (
            '<div class="hero-shell">'
            '<div class="metric-strip">'
            f'<div class="metric-card"><div class="metric-label">类</div><div class="metric-value">{workspace["title"]}</div></div>'
            f'<div class="metric-card"><div class="metric-label">项</div><div class="metric-value">{dataset_count}</div></div>'
            f'<div class="metric-card"><div class="metric-label">比</div><div class="metric-value">{compare_candidates}</div></div>'
            f'<div class="metric-card"><div class="metric-label">日</div><div class="metric-value">{date.today().isoformat()}</div></div>'
            "</div></div>"
        ),
        unsafe_allow_html=True,
    )


def _comparison_figure(frame: pd.DataFrame, transform_label: str) -> go.Figure:
    fig = go.Figure()
    for column in [item for item in frame.columns if item != "date"]:
        fig.add_trace(
            go.Scatter(
                x=frame["date"],
                y=frame[column],
                mode="lines",
                connectgaps=True,
                name=column,
                line=dict(width=2.1),
            )
        )
    fig.update_layout(
        height=520,
        margin=dict(l=50, r=30, t=50, b=40),
        paper_bgcolor="rgba(255,255,255,0.92)",
        plot_bgcolor="rgba(255,255,255,0.92)",
        title=dict(text=f"<b>多序列对比</b><br><sup>{transform_label}</sup>", x=0.02, xanchor="left"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.02),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(19,34,56,0.08)", tickformat="%Y-%m")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(19,34,56,0.08)")
    return fig


def _set_selected_dataset(dataset_id: str) -> None:
    st.session_state["terminal_selected_dataset"] = dataset_id


def _is_manual_source(source_id: str) -> bool:
    return source_id.startswith("manual-excel")


def _render_spotlight_views(
    workspace: dict,
    datasets: list[dict],
    start_date: str,
    end_date: str,
) -> None:
    columns_per_row = 4
    available_dataset_ids = {dataset["id"] for dataset in datasets}
    workspace_views = [
        view for view in list_views(workspace_id=workspace["id"]) if view["id"] in set(workspace.get("view_ids", []))
    ]
    workspace_views = [view for view in workspace_views if view["dataset_id"] in available_dataset_ids]

    if workspace_views:
        prioritized_ids = [view["dataset_id"] for view in workspace_views]
        ordered_datasets = [get_dataset(dataset_id) for dataset_id in prioritized_ids]
        remaining = [dataset for dataset in datasets if dataset["id"] not in set(prioritized_ids)]
        display_datasets = ordered_datasets + remaining
    else:
        display_datasets = datasets

    for start_index in range(0, len(display_datasets), columns_per_row):
        row = display_datasets[start_index : start_index + columns_per_row]
        columns = st.columns(columns_per_row)
        for column, dataset in zip(columns, row):
            matching_view = next((view for view in workspace_views if view["dataset_id"] == dataset["id"]), None)
            view_id = matching_view["id"] if matching_view else f"{dataset['id']}.default"
            transform_name = matching_view.get("default_transform", "identity") if matching_view else "identity"
            rolling = int(matching_view.get("default_rolling_window", 1)) if matching_view else 1
            with column:
                with st.container(border=True):
                    st.markdown(
                        f'<div class="dataset-title">{dataset["title"]}</div>',
                        unsafe_allow_html=True,
                    )
                    view_payload, plot_df, _ = build_view_dataset(
                        view_id,
                        start_date=start_date,
                        end_date=end_date,
                        transform_name=transform_name,
                        rolling_window=rolling,
                    )
                    indicator = view_payload["indicator"]
                    if plot_df.empty:
                        st.info("当前时间范围内没有图表数据。")
                    else:
                        figure = build_chart_figure(plot_df, indicator, compact=True)
                        st.plotly_chart(
                            figure,
                            width="stretch",
                            key=f"workspace-view-{view_id}-{start_date}-{end_date}",
                        )
                    _render_action_links(
                        dataset["id"],
                        start_date,
                        end_date,
                        transform_name,
                        rolling,
                    )


def _render_workspace_tab(
    workspace: dict,
    datasets: list[dict],
    start_date: str,
    end_date: str,
    transform_name: str,
    rolling_window: int,
) -> None:
    with st.container(border=True):
        if not datasets:
            st.info("当前筛选条件下没有可展示的数据集。")
            return

        _render_spotlight_views(workspace, datasets, start_date, end_date)


def _render_detail_panel(
    dataset_id: str | None,
    start_date: str,
    end_date: str,
    transform_name: str,
    rolling_window: int,
) -> None:
    if not dataset_id:
        return
    dataset = get_dataset(dataset_id)
    view_id = f"{dataset_id}.default"
    view_payload, plot_df, raw_df = build_view_dataset(
        view_id,
        start_date=start_date,
        end_date=end_date,
        transform_name=transform_name if dataset["supports_compare"] else "identity",
        rolling_window=rolling_window,
    )
    indicator = view_payload["indicator"]
    with st.container(border=True):
        st.markdown(f'<div class="detail-title">{dataset["title"]}</div>', unsafe_allow_html=True)
        st.markdown(
            (
                '<div class="detail-meta">'
                f'数据集: {dataset["id"]} | 图表类型: {dataset["chart_type"]} | 最近更新: {_format_value(dataset["updated_at"])}'
                "</div>"
            ),
            unsafe_allow_html=True,
        )

        header_cols = st.columns([1.2, 1.2, 1.2, 2.4])
        header_cols[0].metric("最新值", _format_value(dataset["latest_value"]))
        header_cols[1].metric("最新日期", _format_value(dataset["latest_date"]))
        header_cols[2].metric("序列数", str(len(dataset["series_codes"]) or 1))
        header_cols[3].write("标签：" + (" / ".join(dataset["tags"]) if dataset["tags"] else "-"))

        if plot_df.empty:
            st.info("当前时间范围内没有图表数据。")
        else:
            figure = build_chart_figure(plot_df, indicator, compact=False)
            st.plotly_chart(figure, width="stretch", key=f"detail-chart-{dataset_id}")

        with st.expander("查看原始数据", expanded=False):
            st.dataframe(raw_df, width="stretch", hide_index=True)
            st.download_button(
                "下载 Excel",
                data=_build_excel_bytes(raw_df),
                file_name=f"{dataset_id}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"download-detail-{dataset_id}",
                width="stretch",
            )


def _render_compare_tab(
    datasets: list[dict],
    workspace: dict,
    start_date: str,
    end_date: str,
    default_transform: str,
    default_rolling_window: int,
) -> None:
    with st.container(border=True):
        st.markdown('<div class="section-title">对比工作台</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="section-copy">这里不再受原始主题图表限制。你可以临时挑多条时间序列，做基期归一、日变化率或标准分对比。</div>',
            unsafe_allow_html=True,
        )
        compare_ready = [item for item in datasets if item["supports_compare"]]
        default_selection = [item["id"] for item in compare_ready[: min(3, len(compare_ready))]]
        selected_ids = st.multiselect(
            "选择要对比的数据集",
            options=[item["id"] for item in compare_ready],
            default=default_selection,
            format_func=lambda dataset_id: get_dataset(dataset_id)["title"],
        )
        transform_name = st.selectbox(
            "对比变换",
            options=list(TRANSFORM_LABELS.keys()),
            index=max(0, list(TRANSFORM_LABELS.keys()).index(default_transform if default_transform in TRANSFORM_LABELS else "rebase")),
            format_func=lambda item: TRANSFORM_LABELS[item],
            key="compare-transform",
        )
        rolling_window = st.slider(
            "平滑窗口",
            min_value=1,
            max_value=12,
            value=default_rolling_window,
            help="对比图的简单滚动均值窗口，1 表示不平滑。",
        )
        if len(selected_ids) < 2:
            st.info("至少选择 2 条可对比序列。")
            return

        comparison = build_comparison_dataset(
            selected_ids,
            start_date=start_date,
            end_date=end_date,
            transform_name=transform_name,
            rolling_window=rolling_window,
        )
        if comparison.empty:
            st.warning("当前选择的数据集在这个时间范围内没有可对比数据。")
            return

        st.plotly_chart(_comparison_figure(comparison, TRANSFORM_LABELS[transform_name]), width="stretch")
        summary = comparison.drop(columns=["date"]).describe().T.reset_index().rename(columns={"index": "dataset_id"})
        summary["title"] = summary["dataset_id"].apply(lambda dataset_id: get_dataset(dataset_id)["title"])
        st.dataframe(summary[["title", "mean", "std", "min", "max"]], width="stretch", hide_index=True)


def _render_catalog_tab(datasets: list[dict], search_results: dict, search_text: str) -> None:
    with st.container(border=True):
        st.markdown('<div class="section-title">数据目录</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="catalog-hint">目录页现在会统一展示 dataset、view 和 workspace。输入关键词时，可以把它当作一个轻量的全局研究入口。</div>',
            unsafe_allow_html=True,
        )
        if not datasets:
            st.info("没有匹配的数据集。")
        else:
            catalog = pd.DataFrame(datasets)[
                ["title", "id", "workspace_title", "latest_value", "latest_date", "updated_at"]
            ].rename(
                columns={
                    "title": "名称",
                    "id": "数据集ID",
                    "workspace_title": "分类",
                    "latest_value": "最新值",
                    "latest_date": "最近日期",
                    "updated_at": "更新时间",
                }
            )
            st.dataframe(catalog, width="stretch", hide_index=True)

        if not search_text.strip():
            st.caption("输入关键词后，这里还会显示匹配的视图和分类。")
            return

        view_matches = search_results["views"]
        workspace_matches = search_results["workspaces"]

        if workspace_matches:
            st.markdown("**匹配的分类**")
            workspace_table = pd.DataFrame(workspace_matches)[["title", "id", "description"]].rename(
                columns={"title": "名称", "id": "分类ID", "description": "描述"}
            )
            st.dataframe(workspace_table, width="stretch", hide_index=True)

        if view_matches:
            st.markdown("**匹配的视图**")
            view_table = pd.DataFrame(view_matches)[
                ["title", "id", "dataset_title", "workspace_title", "chart_type"]
            ].rename(
                columns={
                    "title": "名称",
                    "id": "视图ID",
                    "dataset_title": "数据集",
                    "workspace_title": "分类",
                    "chart_type": "图表类型",
                }
            )
            st.dataframe(view_table, width="stretch", hide_index=True)

        if not workspace_matches and not view_matches and not datasets:
            st.info("当前关键词没有匹配到 dataset、view 或 workspace。")


def _render_status_tab(workspace: dict, datasets: list[dict]) -> None:
    with st.container(border=True):
        st.markdown('<div class="section-title">数据状态</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="section-copy">这里集中看工作区里的数据新鲜度，以及最近一次导入记录。</div>',
            unsafe_allow_html=True,
        )
        if not datasets:
            st.info("当前工作区没有可展示的数据集。")
            return

        import_logs = load_latest_manual_imports([dataset["id"] for dataset in datasets])
        status_rows: list[dict[str, str]] = []
        for dataset in datasets:
            log = import_logs.get(dataset["id"], {})
            status_rows.append(
                {
                    "名称": dataset["title"],
                    "数据集ID": dataset["id"],
                    "最近日期": _format_value(dataset["latest_date"]),
                    "图表更新时间": _format_value(dataset["updated_at"]),
                    "最近导入": _format_value(log.get("imported_at")),
                    "导入状态": str(log.get("status", "-")),
                }
            )
        st.dataframe(pd.DataFrame(status_rows), width="stretch", hide_index=True)


def main() -> None:
    st.set_page_config(page_title="金融数据终端", layout="wide")
    _apply_terminal_style()

    panel = st.query_params.get("panel")
    dataset_id = st.query_params.get("dataset")
    if panel in {"chart", "data"} and dataset_id:
        start_date = str(st.query_params.get("start") or "2018-01-01")
        end_date = str(st.query_params.get("end") or date.today().isoformat())
        transform_name = str(st.query_params.get("transform") or "identity")
        rolling_window = int(st.query_params.get("window") or 1)
        if panel == "chart":
            _render_dataset_chart_page(dataset_id, start_date, end_date, transform_name, rolling_window)
        else:
            _render_dataset_data_page(dataset_id, start_date, end_date)
        return

    workspaces = list_workspaces()
    if not workspaces:
        st.error("当前没有可用工作区，请先准备指标配置和数据库。")
        return

    workspace_options = [item["id"] for item in workspaces]
    selected_workspace_id = st.sidebar.selectbox(
        "类别",
        options=workspace_options,
        format_func=lambda item: next(workspace["title"] for workspace in workspaces if workspace["id"] == item),
    )
    workspace = get_workspace(selected_workspace_id)
    search_text = st.sidebar.text_input("搜索对象", placeholder="例如 SOFR / 铜 / DXY / 流动性")
    start_date = st.sidebar.date_input("开始日期", value=pd.Timestamp("2018-01-01").date(), key="terminal-start")
    end_date = st.sidebar.date_input("结束日期", value=pd.Timestamp(date.today().isoformat()).date(), key="terminal-end")
    transform_name = st.sidebar.selectbox(
        "默认变换",
        options=list(TRANSFORM_LABELS.keys()),
        index=0,
        format_func=lambda item: TRANSFORM_LABELS[item],
    )
    rolling_window = st.sidebar.slider("默认平滑窗口", min_value=1, max_value=12, value=1)

    if start_date > end_date:
        st.sidebar.error("开始日期不能晚于结束日期。")
        return

    datasets = list_datasets(query=search_text, workspace_id=selected_workspace_id)
    search_results = search_registry(query=search_text, workspace_id=selected_workspace_id)
    compare_candidates = len([item for item in datasets if item["supports_compare"]])
    _render_hero(workspace, len(datasets), compare_candidates)

    tabs = st.tabs(["工作台", "详情", "对比", "搜索", "状态"])
    with tabs[0]:
        _render_workspace_tab(
            workspace,
            datasets,
            start_date.isoformat(),
            end_date.isoformat(),
            transform_name,
            rolling_window,
        )
    with tabs[1]:
        _render_detail_panel(
            st.session_state.get("terminal_selected_dataset") or (datasets[0]["id"] if datasets else None),
            start_date.isoformat(),
            end_date.isoformat(),
            transform_name,
            rolling_window,
        )
    with tabs[2]:
        _render_compare_tab(
            datasets,
            workspace,
            start_date.isoformat(),
            end_date.isoformat(),
            transform_name,
            rolling_window,
        )
    with tabs[3]:
        _render_catalog_tab(datasets, search_results, search_text)
    with tabs[4]:
        _render_status_tab(workspace, datasets)


if __name__ == "__main__":
    main()
