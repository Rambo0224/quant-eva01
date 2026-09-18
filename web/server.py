from __future__ import annotations

import tomllib
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

import duckdb
import pandas as pd
import tomli_w
from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from dataclasses import replace

from macro_replay.config import load_indicators, load_themes, load_theme_settings, save_theme_settings
from macro_replay.db import DB_PATH
from macro_replay.source_labels import display_source_label

CONFIG_PATH = Path("config/mcp_servers.toml")
TEMPLATES_PATH = Path("web/templates")

app = FastAPI(title="Macro Replay Dashboard")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
)


@app.middleware("http")
async def no_cache_charts(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/charts/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response
app.mount("/charts", StaticFiles(directory="charts"), name="charts")

env = Environment(loader=FileSystemLoader(str(TEMPLATES_PATH)), autoescape=select_autoescape(["html"]))


class ServerRecord:
    def __init__(self, name: str, url: str, auth_token: str):
        self.name = name
        self.url = url
        self.auth_token = auth_token


def load_servers() -> List[ServerRecord]:
    if not CONFIG_PATH.exists():
        raise HTTPException(status_code=404, detail="config/mcp_servers.toml not found")
    data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return [ServerRecord(name, entry.get("url", ""), entry.get("auth_token", "")) for name, entry in data.items()]


def save_servers(records: List[ServerRecord]) -> None:
    payload: Dict[str, Dict[str, str]] = {}
    for rec in records:
        if not rec.url:
            raise HTTPException(status_code=400, detail=f"{rec.name} missing URL")
        if not rec.auth_token:
            raise HTTPException(status_code=400, detail=f"{rec.name} missing Authorization")
        payload[rec.name] = {"url": rec.url, "auth_token": rec.auth_token}
    CONFIG_PATH.write_text(tomli_w.dumps(payload), encoding="utf-8")


def _asset_cache_token(path_value: Optional[str], rendered) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if not path.exists():
        return ""
    try:
        stat = path.stat()
        # Use file mtime+size for deterministic cache busting even if DB timestamp lags.
        return f"{int(stat.st_mtime)}-{stat.st_size}"
    except OSError:
        if rendered is None:
            return ""
        return rendered.isoformat() if hasattr(rendered, "isoformat") else str(rendered)


def load_cards() -> List[Dict[str, str]]:
    indicators = {i.id: i for i in load_indicators()}
    chart_rows: Dict[str, Dict[str, str]] = {}
    if DB_PATH.exists():
        try:
            conn = duckdb.connect(str(DB_PATH), read_only=True)
            try:
                for row in conn.execute("SELECT indicator_id, html_path, image_path, rendered_at, source_summary FROM charts").fetchall():
                    chart_rows[row[0]] = {
                        "html": row[1],
                        "image": row[2],
                        "rendered": row[3],
                        "source": row[4],
                    }
            finally:
                conn.close()
        except duckdb.Error:
            chart_rows = {}

    cards = []
    for ind_id, indicator in indicators.items():
        chart = chart_rows.get(ind_id, {})
        image_path = chart.get("image")
        html_path = chart.get("html")
        rendered = chart.get("rendered")
        cache_token = _asset_cache_token(image_path or html_path, rendered)
        cards.append(
            {
                "id": ind_id,
                "title": indicator.title,
                "description": indicator.description,
                "theme": indicator.theme,
                "source": display_source_label(chart.get("source") or indicator.source),
                "updated": rendered,
                "image_url": f"/charts/{Path(image_path).name}?v={cache_token}" if image_path else None,
                "html_path": Path(html_path).name if html_path else None,
                "html_url": f"/charts/{Path(html_path).name}?v={cache_token}" if html_path else None,
                "data_url": f"/data/{ind_id}",
            }
        )
    return cards


def load_sections() -> List[Dict[str, object]]:
    cards = load_cards()
    theme_settings = load_theme_settings()
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for card in cards:
        grouped.setdefault(card["theme"], []).append(card)

    today = date.today().isoformat()
    sections: List[Dict[str, object]] = []
    known = set()
    for theme in load_themes():
        known.add(theme.key)
        settings = theme_settings.get(theme.key, {})
        default_enabled = "true" if settings else "false"
        is_enabled = settings.get("enabled", default_enabled).lower() == "true"
        sections.append(
            {
                "key": theme.key,
                "title": theme.title,
                "description": theme.description,
                "cards": grouped.get(theme.key, []) if is_enabled else [],
                "start_date": settings.get("start_date", "2018-01-01"),
                "end_date": today,
                "enabled": is_enabled,
            }
        )
    for theme_key, cards_in_theme in grouped.items():
        if theme_key in known:
            continue
        settings = theme_settings.get(theme_key, {})
        default_enabled = "true" if settings else "false"
        is_enabled = settings.get("enabled", default_enabled).lower() == "true"
        sections.append(
            {
                "key": theme_key,
                "title": theme_key,
                "description": "",
                "cards": cards_in_theme if is_enabled else [],
                "start_date": settings.get("start_date", "2018-01-01"),
                "end_date": today,
                "enabled": is_enabled,
            }
        )
    return sections


def load_indicator_rows(indicator_id: str) -> List[Dict[str, str]]:
    if not DB_PATH.exists():
        return []
    try:
        conn = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT obs_time, series_id, source_id, value, unit
                FROM observations
                WHERE indicator_id = ?
                ORDER BY obs_time DESC, series_id
                """,
                [indicator_id],
            ).fetchall()
        finally:
            conn.close()
    except duckdb.Error:
        return []
    return [
        {
            "obs_time": row[0],
            "series_id": row[1],
            "source_id": display_source_label(row[2]),
            "value": row[3],
            "unit": row[4] or "",
        }
        for row in rows
    ]


def resolve_indicator(indicator_id: str):
    indicator_map = {item.id: item for item in load_indicators()}
    indicator = indicator_map.get(indicator_id)
    if indicator is None:
        raise HTTPException(status_code=404, detail=f"Indicator not found: {indicator_id}")
    return indicator


def indicator_with_theme_settings(indicator):
    settings = load_theme_settings().get(indicator.theme, {})
    args = dict(indicator.arguments)
    if settings.get("start_date"):
        args["start_date"] = settings["start_date"]
    args["end_date"] = date.today().isoformat()
    return replace(indicator, arguments=args)


def refresh_indicator_full(indicator_id: str) -> None:
    resolve_indicator(indicator_id)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    sections = load_sections()
    template = env.get_template("dashboard.html")
    return template.render(sections=sections)


@app.get("/config", response_class=HTMLResponse)
async def config_page():
    records = load_servers()
    template = env.get_template("servers.html")
    return template.render(records=records)


@app.post("/refresh-theme")
async def refresh_theme(
    theme_key: str = Form(...),
    start_date: str = Form(...),
):
    today = date.today().isoformat()
    settings = load_theme_settings()
    settings[theme_key] = {"start_date": start_date, "enabled": "true"}
    save_theme_settings(settings)
    return RedirectResponse("/", status_code=303)


@app.get("/data/{indicator_id}.xlsx")
async def data_excel(indicator_id: str):
    indicator = resolve_indicator(indicator_id)
    rows = load_indicator_rows(indicator_id)
    frame = pd.DataFrame(rows, columns=["obs_time", "series_id", "source_id", "value", "unit"])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="raw_data")
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename=\"{indicator_id}.xlsx\"'},
    )


@app.get("/data/{indicator_id}", response_class=HTMLResponse)
async def data_page(indicator_id: str):
    indicator = resolve_indicator(indicator_id)
    rows = load_indicator_rows(indicator_id)
    template = env.get_template("data.html")
    return template.render(indicator=indicator, rows=rows)


@app.post("/data/{indicator_id}/refresh-full")
async def data_refresh_full(indicator_id: str):
    resolve_indicator(indicator_id)
    refresh_indicator_full(indicator_id)
    return RedirectResponse(f"/data/{indicator_id}", status_code=303)


@app.post("/save")
async def save(
    names: List[str] = Form(...),
    urls: List[str] = Form(...),
    tokens: List[str] = Form(...),
):
    records = [ServerRecord(name, url, token) for name, url, token in zip(names, urls, tokens)]
    save_servers(records)
    return RedirectResponse("/config", status_code=303)
