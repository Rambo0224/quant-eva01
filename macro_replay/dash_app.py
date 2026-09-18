from __future__ import annotations

import base64
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import time

import pandas as pd
from dash import Dash, Input, Output, State, dash_table, dcc, html
from dash.exceptions import PreventUpdate
from flask import Response, abort, send_file

from .dashboard_service import (
    build_indicator_dataset,
    get_indicator,
    list_themes,
    load_dashboard_cards,
    load_display_window,
    save_display_window,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


@lru_cache(maxsize=512)
def _encode_image_data_uri(path_str: str, mtime_ns: int) -> str:
    payload = Path(path_str).read_bytes()
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _card_image_src(card: dict) -> str | None:
    image_path = card.get("image_path")
    if not image_path:
        return None
    path = PROJECT_ROOT / image_path
    if not path.exists():
        return None
    return _encode_image_data_uri(str(path), path.stat().st_mtime_ns)


def _format_value(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


def _build_topbar() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("复盘面板", className="topbar-title"),
                    html.Div("Dash 工作台", className="topbar-subtitle"),
                ]
            ),
            html.Div(
                "总览页默认展开全部指标，按主题单独刷新；交互图和原始数据保留独立页面。",
                className="topbar-note",
            ),
        ],
        className="topbar",
    )


def _build_card(card: dict) -> html.Div:
    image_src = _card_image_src(card)
    image_block = (
        html.Img(src=image_src, className="indicator-thumb")
        if image_src
        else html.Div("暂无预览图", className="indicator-thumb indicator-thumb-empty")
    )
    updated = card.get("updated_at")
    updated_text = (
        pd.Timestamp(updated).strftime("%Y-%m-%d %H:%M")
        if updated is not None
        else "-"
    )
    latest_date = (
        pd.Timestamp(card["latest_date"]).strftime("%Y-%m-%d")
        if card.get("latest_date") is not None
        else "-"
    )
    return html.Div(
        [
            html.Div(
                [
                    html.Div(card["title"], className="card-title"),
                    html.Div(card.get("description") or "", className="card-desc"),
                ],
                className="card-copy",
            ),
            html.Div(
                [
                    html.Span(f"最新值: {_format_value(card.get('latest_value'))}", className="card-chip"),
                    html.Span(f"日期: {latest_date}", className="card-chip"),
                ],
                className="card-meta",
            ),
            html.Div(image_block, className="thumb-shell"),
            html.Div(
                [
                    dcc.Link("查看交互图", href=f"/chart/{card['id']}", className="card-link"),
                    dcc.Link("查看原始数据", href=f"/data/{card['id']}", className="card-link"),
                ],
                className="card-actions",
            ),
            html.Div(f"更新于 {updated_text}", className="card-updated"),
        ],
        className="indicator-card",
    )


def _build_theme_section(theme) -> html.Section:
    cards = load_dashboard_cards(theme.key)
    return html.Section(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(theme.title, className="section-title"),
                            html.Div(theme.description or "", className="section-desc"),
                        ],
                        className="section-copy",
                    ),
                ],
                className="section-head",
            ),
            html.Div([_build_card(card) for card in cards], className="card-grid"),
        ],
        className="theme-section",
    )


def _build_overview_page() -> html.Div:
    state = load_display_window()
    return html.Div(
        [
            _build_topbar(),
            html.Div(
                [
                    dcc.DatePickerRange(
                        id="global-date-window",
                        start_date=state["start_date"],
                        end_date=state["end_date"],
                        display_format="YYYY-MM-DD",
                        minimum_nights=0,
                        className="theme-range",
                    ),
                    html.Button(
                        "刷新面板",
                        id="global-refresh",
                        className="theme-refresh-btn",
                        n_clicks=0,
                    ),
                ],
                className="section-tools",
            ),
            html.Div([_build_theme_section(theme) for theme in list_themes()], className="main-scroll"),
        ],
        className="overview-page",
    )


def _current_indicator_window(indicator_id: str) -> tuple[str, str]:
    state = load_display_window()
    return state["start_date"], state["end_date"]


def _build_chart_page(indicator_id: str) -> html.Div:
    indicator = get_indicator(indicator_id)
    card = next((item for item in load_dashboard_cards() if item["id"] == indicator_id), None)
    html_path = card.get("html_path") if card else None
    if not html_path:
        body = html.Div("交互图尚未生成，请先刷新对应模块。", className="detail-empty")
    else:
        body = html.Iframe(src=f"/chart-file/{indicator_id}", className="detail-frame")
    return html.Div(
        [
            html.Div(
                [
                    dcc.Link("返回总览", href="/", className="detail-back"),
                    html.Div(indicator.title, className="detail-title"),
                    html.Div(indicator.description or "", className="detail-desc"),
                ],
                className="detail-header",
            ),
            body,
        ],
        className="detail-page",
    )


def _build_data_page(indicator_id: str) -> html.Div:
    indicator, _, raw_df = build_indicator_dataset(indicator_id, *_current_indicator_window(indicator_id))
    records = raw_df.copy()
    if "date" in records.columns:
        records["date"] = records["date"].dt.strftime("%Y-%m-%d")
    if "extra" in records.columns:
        records["extra"] = records["extra"].apply(lambda value: "" if value in ({}, None) else str(value))

    return html.Div(
        [
            html.Div(
                [
                    dcc.Link("返回总览", href="/", className="detail-back"),
                    html.Div(indicator.title, className="detail-title"),
                    html.Div(indicator.description or "", className="detail-desc"),
                    html.A("下载原始数据", href=f"/download-raw/{indicator_id}", className="detail-download"),
                ],
                className="detail-header",
            ),
            dash_table.DataTable(
                columns=[{"name": column, "id": column} for column in records.columns],
                data=records.to_dict("records"),
                page_size=25,
                sort_action="native",
                filter_action="native",
                style_table={"overflowX": "auto"},
                style_cell={
                    "textAlign": "left",
                    "padding": "10px 12px",
                    "fontSize": "12px",
                    "maxWidth": "280px",
                    "whiteSpace": "normal",
                    "height": "auto",
                    "border": "none",
                },
                style_header={
                    "backgroundColor": "#f8fafc",
                    "fontWeight": "600",
                    "borderBottom": "1px solid #e2e8f0",
                },
                style_data={"backgroundColor": "#ffffff", "borderBottom": "1px solid #eef2f7"},
            ),
        ],
        className="detail-page",
    )


app = Dash(
    __name__,
    title="复盘面板 · Dash",
    assets_folder=str(PROJECT_ROOT / "assets"),
    suppress_callback_exceptions=True,
)
server = app.server


@server.get("/chart-image/<indicator_id>")
def chart_image(indicator_id: str):
    card = next((item for item in load_dashboard_cards() if item["id"] == indicator_id), None)
    image_path = card.get("image_path") if card else None
    if not image_path:
        abort(404)
    path = PROJECT_ROOT / image_path
    if not path.exists():
        abort(404)
    return send_file(path)


@server.get("/chart-file/<indicator_id>")
def chart_file(indicator_id: str):
    card = next((item for item in load_dashboard_cards() if item["id"] == indicator_id), None)
    html_path = card.get("html_path") if card else None
    if not html_path:
        abort(404)
    path = PROJECT_ROOT / html_path
    if not path.exists():
        abort(404)
    return send_file(path)


@server.get("/download-raw/<indicator_id>")
def download_raw(indicator_id: str):
    _, _, raw_df = build_indicator_dataset(indicator_id, *_current_indicator_window(indicator_id))
    payload = _build_excel_bytes(raw_df)
    return Response(
        payload,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{indicator_id}.xlsx"'},
    )


app.layout = html.Div(
    [
        dcc.Location(id="url"),
        dcc.Store(id="refresh-token", data=time.time()),
        html.Div(id="flash-message", className="flash-message"),
        html.Div(id="app-shell"),
    ],
    className="app-root",
)


@app.callback(
    Output("refresh-token", "data"),
    Output("flash-message", "children"),
    Input("global-refresh", "n_clicks"),
    State("global-date-window", "start_date"),
    State("global-date-window", "end_date"),
    prevent_initial_call=True,
)
def handle_refresh(_, start_date, end_date):
    if not start_date or not end_date:
        raise PreventUpdate
    save_display_window(start_date, end_date)
    stamp = time.time()
    return stamp, f"{datetime.now().strftime('%H:%M:%S')} 已按统一日期窗口刷新"


@app.callback(
    Output("app-shell", "children"),
    Input("url", "pathname"),
    Input("url", "search"),
    Input("refresh-token", "data"),
)
def render_app(pathname: str, search: str, _):
    indicator_id = None
    if pathname.startswith("/chart/"):
        indicator_id = pathname.split("/chart/", 1)[1]
        page = _build_chart_page(indicator_id)
    elif pathname.startswith("/data/"):
        indicator_id = pathname.split("/data/", 1)[1]
        page = _build_data_page(indicator_id)
    else:
        page = _build_overview_page()

    return html.Div([html.Div(page, className="content-shell")], className="shell")


def run(host: str = "127.0.0.1", port: int = 8050) -> None:
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run()
