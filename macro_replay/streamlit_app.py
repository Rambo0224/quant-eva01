from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
import re
import sys

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from macro_replay.dashboard_service import (
    build_indicator_dataset,
    list_indicators,
    list_themes,
    load_app_state,
    load_dashboard_cards,
    load_display_window,
    save_app_state,
    save_display_window,
)
from macro_replay.charts import INTERACTIVE_CHART_HEIGHT
from macro_replay.data_quality import chart_data_quality
from macro_replay.pipeline import rerender_theme_from_db
from datahub.adapters.comein_mcp import _load_local_config, save_local_config


def _apply_page_style() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 0.75rem;
            padding-bottom: 0.85rem;
            max-width: 112rem;
        }
        [data-testid="stSidebar"] {
            min-width: 15.5rem;
            max-width: 15.5rem;
        }
        div[data-testid="stVerticalBlock"] div[data-testid="stHorizontalBlock"] {
            gap: 0.5rem;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 12px;
            background: #ffffff;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.04);
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div {
            padding: 0.48rem 0.58rem 0.16rem 0.58rem;
        }
        .compact-title {
            font-size: 0.62rem;
            font-weight: 600;
            line-height: 1.15;
            color: #ffffff;
            margin-bottom: 0.08rem;
        }
        .compact-desc {
            font-size: 0.42rem;
            line-height: 1.2;
            color: rgba(255, 255, 255, 0.72);
            margin-bottom: 0.16rem;
        }
        .detail-link {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            min-height: 2rem;
            padding: 0.32rem 0.5rem;
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 10px;
            background: #f8fafc;
            color: #0f172a;
            text-decoration: none;
            font-size: 0.74rem;
            font-weight: 600;
            box-sizing: border-box;
        }
        .detail-link:hover {
            background: #eef2f7;
            color: #0f172a;
            text-decoration: none;
        }
        .section-header {
            margin: 0.18rem 0 0.35rem 0;
            padding-left: 0.08rem;
        }
        .section-title {
            font-size: 0.96rem;
            font-weight: 700;
            color: #ffffff;
        }
        div[data-testid="stPlotlyChart"] {
            margin-top: -0.1rem;
        }
        div[data-testid="stExpander"] details summary p {
            font-size: 0.78rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _build_excel_bytes(raw_df: pd.DataFrame) -> bytes:
    buffer = BytesIO()
    export_df = raw_df.copy()
    if "extra" in export_df.columns:
        export_df["extra"] = export_df["extra"].apply(
            lambda value: "" if value in ({}, None) else str(value)
        )
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="raw_data")
    buffer.seek(0)
    return buffer.getvalue()


def _detail_page_href(indicator_id: str, view: str) -> str:
    return f"?view={view}&indicator={indicator_id}"


def _close_raw_data_page() -> None:
    st.query_params.clear()
    st.rerun()


def _visible_description(indicator) -> str:
    description = (indicator.description or "").strip()
    for prefix in (
        "手动数据库 Excel - ",
        "手动数据库Excel - ",
        "手动数据库 Excel-",
        "手动数据库 - ",
    ):
        if description.startswith(prefix):
            description = description[len(prefix) :].strip()
            break

    def _normalize(value: str) -> str:
        return re.sub(r"[\s\-_/（）()]+", "", value).lower()

    if not description:
        return ""
    if _normalize(description) == _normalize(indicator.title):
        return ""
    return description


def _render_raw_data_page(indicator_id: str) -> None:
    display_window = load_display_window()
    indicator, _, raw_df = build_indicator_dataset(
        indicator_id,
        start_date=display_window["start_date"],
        end_date=display_window["end_date"],
    )
    st.title(f"{indicator.title} 原始数据")
    visible_description = _visible_description(indicator)
    if visible_description:
        st.caption(visible_description)

    top_cols = st.columns([1, 1, 6])
    with top_cols[0]:
        if st.button("返回总览", width="stretch"):
            _close_raw_data_page()
    with top_cols[1]:
        st.download_button(
            "下载原始数据",
            data=_build_excel_bytes(raw_df),
            file_name=f"{indicator.id}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"download-page-{indicator.id}",
            width="stretch",
        )

    st.dataframe(raw_df, width="stretch", hide_index=True)


def _render_chart_page(indicator_id: str) -> None:
    display_window = load_display_window()
    indicator, plot_df, raw_df = build_indicator_dataset(
        indicator_id,
        start_date=display_window["start_date"],
        end_date=display_window["end_date"],
    )
    card_meta = next(
        (item for item in load_dashboard_cards() if item["id"] == indicator_id),
        None,
    )
    html_path = (card_meta or {}).get("html_path")
    html_file = PROJECT_ROOT / html_path if html_path else None

    st.title(f"{indicator.title} 交互图")
    visible_description = _visible_description(indicator)
    if visible_description:
        st.caption(visible_description)

    if st.button("返回总览", width="stretch"):
        _close_raw_data_page()

    usable, reason = chart_data_quality(indicator, raw_df=raw_df, plot_df=plot_df)
    if not usable:
        st.warning(reason)
        return

    if html_file and html_file.exists():
        components.html(
            html_file.read_text(encoding="utf-8"),
            height=INTERACTIVE_CHART_HEIGHT,
            scrolling=False,
        )
    else:
        st.info("交互图 HTML 还没有生成，请先通过启动脚本选择更新数据，或在面板上刷新缩略图。")


def _render_indicator(
    indicator_id: str,
    start_date: str,
    end_date: str,
    show_raw_data: bool,
    card_meta: dict | None,
) -> None:
    indicator, plot_df, raw_df = build_indicator_dataset(
        indicator_id,
        start_date=start_date,
        end_date=end_date,
    )

    usable, quality_reason = chart_data_quality(
        indicator,
        raw_df=raw_df,
        plot_df=plot_df,
    )

    with st.container(border=True):
        st.markdown(
            f'<div class="compact-title">{indicator.title}</div>',
            unsafe_allow_html=True,
        )
        visible_description = _visible_description(indicator)
        if visible_description:
            st.markdown(
                f'<div class="compact-desc">{visible_description}</div>',
                unsafe_allow_html=True,
            )

        if plot_df.empty:
            st.info(quality_reason or "当前时间范围内还没有可展示的数据。")
            return

        if not usable:
            st.warning(quality_reason)
            return

        image_path = (card_meta or {}).get("image_path")
        image_file = PROJECT_ROOT / image_path if image_path else None
        if image_file and image_file.exists():
            st.image(str(image_file), width="stretch")
        else:
            st.info("当前图表预览图还没有生成，请先通过启动脚本选择“更新数据”完成数据准备。")

        html_path = (card_meta or {}).get("html_path")
        html_file = PROJECT_ROOT / html_path if html_path else None
        action_cols = st.columns(2)
        with action_cols[0]:
            if html_file and html_file.exists():
                st.markdown(
                    f'<a class="detail-link" href="{_detail_page_href(indicator.id, "chart")}" target="_blank" rel="noopener noreferrer">查看交互图</a>',
                    unsafe_allow_html=True,
                )
        with action_cols[1]:
            st.markdown(
                f'<a class="detail-link" href="{_detail_page_href(indicator.id, "data")}" target="_blank" rel="noopener noreferrer">查看原始数据</a>',
                unsafe_allow_html=True,
            )

        if show_raw_data:
            with st.expander("查看原始数据", expanded=False):
                st.dataframe(raw_df, width="stretch", hide_index=True)
                st.download_button(
                    "下载 Excel",
                    data=_build_excel_bytes(raw_df),
                    file_name=f"{indicator.id}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"download-raw-{indicator.id}-{start_date}-{end_date}",
                    width="stretch",
                )


def _rerender_dashboard_thumbnails(theme_display_state: dict[str, tuple[str, str]]) -> dict[str, list[str]]:
    report = {"success": [], "failed": []}
    for theme in list_themes():
        start_date, end_date = theme_display_state[theme.key]
        try:
            rerender_theme_from_db(theme.key, start_date, end_date)
            report["success"].append(theme.title)
        except Exception as exc:
            report["failed"].append(f"{theme.title}: {exc}")
    return report


def main() -> None:
    st.set_page_config(page_title="复盘面板", layout="wide")
    _apply_page_style()

    if st.query_params.get("view") == "data" and st.query_params.get("indicator"):
        _render_raw_data_page(st.query_params["indicator"])
        return
    if st.query_params.get("view") == "chart" and st.query_params.get("indicator"):
        _render_chart_page(st.query_params["indicator"])
        return

    themes = list_themes()
    all_indicators = list_indicators()

    app_state = load_app_state()
    column_options = [2, 3, 4]
    try:
        saved_columns_count = int(app_state.get("columns_count", "3"))
    except ValueError:
        saved_columns_count = 3
    if saved_columns_count not in column_options:
        saved_columns_count = 3

    columns_count = st.sidebar.selectbox(
        "每行图表数",
        options=column_options,
        index=column_options.index(saved_columns_count),
    )
    if columns_count != saved_columns_count:
        save_app_state(columns_count)

    show_raw_data = st.sidebar.checkbox("显示原始数据折叠区", value=False)

    local_comein = _load_local_config()
    with st.sidebar.expander("进门财经 MCP", expanded=False):
        comein_url = st.text_input(
            "服务地址",
            value=local_comein.get(
                "COMEIN_MCP_URL",
                "https://mcp-server-global.comein.cn/mcp-servers/mcp-server-brm/sse",
            ),
            key="comein-mcp-url",
        )
        comein_key = st.text_input(
            "x-mcp-key",
            value=local_comein.get("COMEIN_MCP_KEY", ""),
            type="password",
            key="comein-mcp-key",
        )
        status = "已配置" if comein_key else "未配置"
        st.caption(f"当前状态：{status}")
        action_cols = st.columns(2)
        with action_cols[0]:
            if st.button("保存", key="save-comein-mcp", width="stretch"):
                save_local_config(comein_url, comein_key)
                st.success("已保存")
        with action_cols[1]:
            if st.button("清空", key="clear-comein-mcp", width="stretch"):
                save_local_config(comein_url, "")
                st.rerun()

    display_window = load_display_window()
    start_date = st.sidebar.date_input(
        "全局开始日期",
        value=pd.Timestamp(display_window["start_date"]).date(),
        key="global-start-date",
    )
    end_date = st.sidebar.date_input(
        "全局结束日期",
        value=pd.Timestamp(display_window["end_date"] or date.today().isoformat()).date(),
        key="global-end-date",
    )

    if start_date > end_date:
        st.sidebar.error("全局开始日期不能晚于结束日期。")
        return

    start_date_text = start_date.isoformat()
    end_date_text = end_date.isoformat()
    if (
        start_date_text != display_window.get("start_date")
        or end_date_text != display_window.get("end_date")
    ):
        save_display_window(start_date_text, end_date_text)

    theme_display_state: dict[str, tuple[str, str]] = {
        theme.key: (start_date_text, end_date_text)
        for theme in themes
    }

    if "last_panel_refresh_status" in st.session_state:
        status_payload = st.session_state.pop("last_panel_refresh_status")
        failed = status_payload.get("failed", [])
        if failed:
            st.sidebar.warning(f"缩略图重绘完成，但有 {len(failed)} 个模块失败。")
            with st.sidebar.expander("查看失败原因", expanded=False):
                for item in failed:
                    st.caption(item)
        else:
            st.sidebar.success("面板缩略图 PNG 已按当前日期区间从数据库重绘。")

    if st.sidebar.button("刷新面板", key="refresh-panel-only", width="stretch"):
        with st.spinner("正在从数据库读取当前日期区间，并重新生成首页 PNG 缩略图..."):
            refresh_report = _rerender_dashboard_thumbnails(theme_display_state)
        st.cache_data.clear()
        st.session_state["last_panel_refresh_status"] = refresh_report
        st.rerun()

    for theme in themes:
        start_date_text, end_date_text = theme_display_state[theme.key]
        theme_cards = {
            item["id"]: item
            for item in load_dashboard_cards(theme.key)
        }
        st.markdown(
            f'<div class="section-header"><div class="section-title">{theme.title}</div></div>',
            unsafe_allow_html=True,
        )

        theme_indicator_ids = [
            indicator.id for indicator in all_indicators if indicator.theme == theme.key
        ]
        for start_index in range(0, len(theme_indicator_ids), columns_count):
            row_ids = theme_indicator_ids[start_index : start_index + columns_count]
            columns = st.columns(columns_count)
            for column, indicator_id in zip(columns, row_ids):
                with column:
                    _render_indicator(
                        indicator_id,
                        start_date_text,
                        end_date_text,
                        show_raw_data=show_raw_data,
                        card_meta=theme_cards.get(indicator_id),
                    )


if __name__ == "__main__":
    main()
