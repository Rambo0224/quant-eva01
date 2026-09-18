from __future__ import annotations

from datetime import date
from typing import Optional

import typer
import webbrowser

from .config import find_indicator, load_indicators, load_mcp_servers
from .db import ensure_db
from .ingest import fetch_indicator
from .dash_app import run as run_dashui
from .terminal_ui import run as run_terminal
from .webui import run as run_webui

app = typer.Typer(help="Macro replay CLI")


@app.command()
def init_db() -> None:
    ensure_db()
    typer.secho("DuckDB schema ready", fg=typer.colors.GREEN)


@app.command("list-indicators")
def list_indicators(theme: Optional[str] = typer.Option(None, help="Filter by theme")) -> None:
    indicators = load_indicators()
    filtered = [i for i in indicators if not theme or i.theme == theme]
    for ind in filtered:
        typer.echo(f"[{ind.theme}] {ind.id}: {ind.title}")


@app.command()
def fetch(
    indicator: str = typer.Option(..., "--indicator", help="Indicator ID from config"),
    start: str = typer.Option(date.today().replace(year=date.today().year - 1).isoformat(), help="Start date"),
    end: str = typer.Option(date.today().isoformat(), help="End date"),
):
    servers = load_mcp_servers()
    indicators = load_indicators()
    ind = find_indicator(indicators, indicator)
    if ind.server not in servers:
        raise typer.BadParameter(f"Server {ind.server} not configured. Update config/mcp_servers.toml")
    conn = ensure_db()
    response = fetch_indicator(conn, ind, servers[ind.server], start, end)
    typer.echo(response)


@app.command()
def webui(
    host: str = typer.Option("127.0.0.1", help="监听主机"),
    port: int = typer.Option(8000, help="监听端口"),
):
    """启动 Streamlit 复盘面板。"""
    url = f"http://{host}:{port}"
    typer.secho(f"打开 {url} 查看 Streamlit 仪表板", fg=typer.colors.CYAN)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    run_webui(host=host, port=port)


@app.command()
def dashui(
    host: str = typer.Option("127.0.0.1", help="监听主机"),
    port: int = typer.Option(8050, help="监听端口"),
):
    """启动 Dash 稳定原型界面。"""
    url = f"http://{host}:{port}"
    typer.secho(f"打开 {url} 查看 Dash 原型", fg=typer.colors.CYAN)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    run_dashui(host=host, port=port)


@app.command()
def terminal(
    host: str = typer.Option("127.0.0.1", help="监听主机"),
    port: int = typer.Option(8010, help="监听端口"),
):
    """启动金融数据终端原型。"""
    url = f"http://{host}:{port}"
    typer.secho(f"打开 {url} 查看金融数据终端", fg=typer.colors.CYAN)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    run_terminal(host=host, port=port)


if __name__ == "__main__":
    app()
