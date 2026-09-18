from __future__ import annotations

import json
import sys
import threading
import time
import warnings
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from pandas.tseries.offsets import BDay

from .config import Indicator, Theme, find_indicator, load_app_settings, load_indicators, load_mcp_servers, load_theme_settings, load_themes, save_app_settings, save_theme_settings
from .data_quality import chart_data_quality
from .db import DB_PATH
from .pipeline import (
    _apply_calculation,
    _apply_chart_transforms,
    _apply_plot_date_window,
    process_all,
    process_indicator,
    process_theme,
    rerender_theme_from_db,
)
from .source_labels import display_source_label


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_REFRESH_LOG_PATH = PROJECT_ROOT / "logs" / "dashboard_refresh_all.json"


def list_themes() -> list[Theme]:
    return load_themes()


def list_indicators() -> list[Indicator]:
    return load_indicators()


def load_app_state() -> dict[str, str]:
    settings = load_app_settings()
    return {
        "columns_count": settings.get("columns_count", "3"),
    }


def save_app_state(columns_count: int) -> None:
    settings = load_app_settings()
    settings["columns_count"] = str(columns_count)
    save_app_settings(settings)


def load_display_window() -> dict[str, str]:
    settings = load_app_settings()
    if settings.get("start_date") and settings.get("end_date"):
        return {
            "start_date": settings["start_date"],
            "end_date": settings["end_date"],
        }

    theme_settings = load_theme_settings()
    first_theme = next(iter(theme_settings.values()), {}) if theme_settings else {}
    return {
        "start_date": first_theme.get("start_date", "2018-01-01"),
        "end_date": first_theme.get("end_date", date.today().isoformat()),
    }


def save_display_window(start_date: str, end_date: str) -> None:
    settings = load_app_settings()
    settings["start_date"] = start_date
    settings["end_date"] = end_date
    save_app_settings(settings)


def get_indicator(indicator_id: str) -> Indicator:
    return find_indicator(load_indicators(), indicator_id)


def load_theme_state(theme_key: str) -> dict[str, str]:
    settings = load_theme_settings().get(theme_key, {})
    window = load_display_window()
    return {
        "start_date": window["start_date"],
        "end_date": window["end_date"],
        "enabled": settings.get("enabled", "true"),
    }


def save_theme_state(theme_key: str, start_date: str, end_date: str | None = None, enabled: str = "true") -> None:
    settings = load_theme_settings()
    settings[theme_key] = {
        "start_date": start_date,
        "end_date": end_date or date.today().isoformat(),
        "enabled": enabled,
    }
    save_theme_settings(settings)


def refresh_theme(theme_key: str, start_date: str, end_date: str | None = None) -> None:
    today = end_date or date.today().isoformat()
    save_display_window(start_date, today)


def _write_refresh_report(report: dict[str, Any]) -> None:
    DASHBOARD_REFRESH_LOG_PATH.parent.mkdir(exist_ok=True)
    DASHBOARD_REFRESH_LOG_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _print_refresh_progress(
    report: dict[str, Any],
    step_name: str,
    status: str,
    *,
    displayed_percent: int | None = None,
    pulse: int | None = None,
    newline: bool | None = None,
) -> None:
    total = int(report.get("total_steps") or 0)
    completed = int(report.get("completed_steps") or 0)
    if total <= 0:
        sys.stdout.write(f"\r[{completed}] {step_name} {status}")
        sys.stdout.flush()
        return

    percent = min(100, max(0, int(displayed_percent if displayed_percent is not None else completed / total * 100)))
    bar_width = 28
    filled = int(bar_width * percent / 100)
    bar_cells = list("=" * filled + "-" * (bar_width - filled))
    if pulse is not None and 0 <= percent < 100:
        pulse_index = max(filled, pulse % bar_width)
        if pulse_index < bar_width:
            bar_cells[pulse_index] = ">"
    bar = "".join(bar_cells)
    color_start = "\033[92m" if sys.stdout.isatty() else ""
    color_end = "\033[0m" if color_start else ""
    label = f"{step_name} {status}".strip()
    line = f"\r{color_start}{bar}{color_end} {percent:3d}% {completed}/{total} {label}"
    sys.stdout.write(line.ljust(120))
    should_newline = (completed >= total or status == "failed") if newline is None else newline
    if should_newline:
        sys.stdout.write("\n")
    sys.stdout.flush()


def _animate_refresh_progress(
    report: dict[str, Any],
    step_name: str,
    stop_event: threading.Event,
) -> None:
    total = int(report.get("total_steps") or 0)
    if total <= 0:
        return

    base_completed = int(report.get("completed_steps") or 0)
    base_percent = int(base_completed / total * 100)
    # A long-running step may not expose internal progress. Move smoothly toward
    # the next step boundary, but leave a small gap for the real completion tick.
    step_ceiling = min(99, int((base_completed + 0.92) / total * 100))
    pulse = 0
    started = time.monotonic()
    while not stop_event.wait(0.18):
        elapsed = time.monotonic() - started
        easing = elapsed / (elapsed + 12.0)
        displayed_percent = base_percent + int((step_ceiling - base_percent) * easing)
        _print_refresh_progress(
            report,
            step_name,
            "running",
            displayed_percent=displayed_percent,
            pulse=pulse,
            newline=False,
        )
        pulse += 1


def _latest_completed_daily_date(anchor: str | None = None) -> str:
    """Return the safest daily-frequency end date for startup data refreshes.

    Public daily A-share/ETF providers often publish today's complete bar after
    the session and settlement pipeline finish. Startup refreshes therefore use
    the previous business day by default; the dashboard display date remains a
    separate UI setting.
    """
    base = pd.Timestamp(anchor or date.today().isoformat()).normalize()
    return (base - BDay(1)).date().isoformat()


def _run_refresh_step(report: dict[str, Any], name: str, func, *args, **kwargs) -> None:
    started_at = datetime.now(timezone.utc)
    step: dict[str, Any] = {
        "name": name,
        "phase": "render" if name.startswith("rerender_theme:") else ("compute" if name.startswith("compute_") else "fetch"),
        "started_at": started_at.isoformat(),
        "status": "running",
    }
    report["steps"].append(step)
    report["current_step"] = name
    report["status"] = "running"
    _write_refresh_report(report)
    _print_refresh_progress(report, name, "starting", newline=False)
    stop_progress = threading.Event()
    progress_thread = threading.Thread(
        target=_animate_refresh_progress,
        args=(report, name, stop_progress),
        daemon=True,
    )
    progress_thread.start()
    try:
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            result = func(*args, **kwargs)
        step["result"] = result
        if caught_warnings:
            step["warnings"] = [
                {
                    "category": warning.category.__name__,
                    "message": str(warning.message),
                }
                for warning in caught_warnings
            ]
        if isinstance(result, dict) and result.get("failed"):
            step["status"] = "failed"
            step["error"] = "step report contains failed items"
        else:
            step["status"] = "success"
    except Exception as exc:
        step["status"] = "failed"
        step["error"] = str(exc)
    finally:
        stop_progress.set()
        progress_thread.join(timeout=1.0)
        step["finished_at"] = datetime.now(timezone.utc).isoformat()
        completed = sum(1 for item in report["steps"] if item.get("status") in {"success", "failed"})
        total = int(report.get("total_steps") or max(completed, 1))
        report["completed_steps"] = completed
        report["progress_percent"] = min(99, int(completed / total * 100))
        _write_refresh_report(report)
        status_label = "done" if step["status"] == "success" else "failed"
        _print_refresh_progress(report, name, status_label)


def refresh_all_dashboard(end_date: str | None = None) -> dict[str, Any]:
    """Incrementally update every data source, then rerender every dashboard chart."""
    from datahub.sync.service import preparation_steps, normalize, derived_steps, default_end

    today = end_date or date.today().isoformat()
    data_end = end_date or default_end()
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "incremental_dashboard_refresh",
        "end_date": today,
        "data_end_date": data_end,
        "status": "starting",
        "steps": [],
    }

    display_window = load_display_window()
    themes = load_themes()
    steps = preparation_steps(today, data_end, configured=process_all)
    report["total_steps"] = len(steps) + 1 + len(derived_steps()) + len(themes)
    _write_refresh_report(report)

    for name, func, args in steps:
        _run_refresh_step(report, name, func, *args)

    # Independent derived work and publication must not be blocked by another source.
    for name, func, args in derived_steps():
        _run_refresh_step(report, name, func, *args)
    _run_refresh_step(report, "normalize_data", normalize, data_end)

    failed_preparation_steps = [step for step in report["steps"] if step.get("status") == "failed"]
    if failed_preparation_steps:
        report["status"] = "failed"
        report["current_step"] = ""
        report["completed_steps"] = len(report["steps"])
        report["progress_percent"] = 100
        report["skipped_steps"] = [
            {
                "name": f"rerender_theme:{theme.key}",
                "phase": "render",
                "reason": "data preparation failed; dashboard render was not refreshed",
            }
            for theme in themes
        ]
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_refresh_report(report)
        _print_refresh_progress(report, "data_preparation", "failed", displayed_percent=100)
        print(f"[done] dashboard refresh {report['status']}", flush=True)
        return report

    for theme in themes:
        _run_refresh_step(
            report,
            f"rerender_theme:{theme.key}",
            rerender_theme_from_db,
            theme.key,
            display_window.get("start_date", "2018-01-01"),
            display_window.get("end_date") or today,
        )

    report["status"] = "failed" if any(step["status"] == "failed" for step in report["steps"]) else "success"
    report["current_step"] = ""
    report["completed_steps"] = len(report["steps"])
    report["progress_percent"] = 100
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_refresh_report(report)
    print(f"[done] dashboard refresh {report['status']}", flush=True)
    return report


def refresh_indicator(indicator_id: str, start_date: str, end_date: str | None = None) -> None:
    indicator = get_indicator(indicator_id)
    today = end_date or date.today().isoformat()
    args = dict(indicator.arguments)
    args["start_date"] = start_date
    args["end_date"] = today
    configured_indicator = replace(indicator, arguments=args)
    try:
        servers = load_mcp_servers()
    except FileNotFoundError:
        servers = {}
    process_indicator(configured_indicator, servers)


def _connect_read_only():
    if not DB_PATH.exists():
        return None
    try:
        return duckdb.connect(str(DB_PATH), read_only=True)
    except duckdb.Error:
        return None


def _empty_observation_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "series_id", "source_id", "value", "unit", "extra"])


def _parse_extra(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def load_observation_rows(indicator_id: str) -> pd.DataFrame:
    conn = _connect_read_only()
    if conn is None:
        return _empty_observation_frame()
    try:
        try:
            rows = conn.execute(
                """
                SELECT obs_time, series_id, source_id, value, unit, extra
                FROM observations
                WHERE indicator_id = ?
                ORDER BY obs_time, series_id
                """,
                [indicator_id],
            ).fetchall()
        except duckdb.Error:
            return _empty_observation_frame()
    finally:
        conn.close()

    if not rows:
        return _empty_observation_frame()

    frame = pd.DataFrame(rows, columns=["date", "series_id", "source_id", "value", "unit", "extra"])
    frame["date"] = pd.to_datetime(frame["date"])
    frame["extra"] = frame["extra"].apply(_parse_extra)
    frame["series_name"] = frame["series_id"].apply(
        lambda value: value.split(":", 1)[1] if ":" in str(value) else str(value)
    )
    frame["source_label"] = frame["source_id"].apply(display_source_label)
    return frame


def build_plot_dataframe(indicator: Indicator, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    rows = load_observation_rows(indicator.id)
    if rows.empty:
        return pd.DataFrame()

    chart_cfg = indicator.chart or {}
    chart_type = chart_cfg.get("type", "line")

    if chart_type == "term_structure":
        frame = rows.copy()
        frame["contract_label"] = frame["extra"].apply(lambda value: value.get("contract_label"))
        frame["curve_label"] = frame["extra"].apply(lambda value: value.get("curve_label"))
        frame["snapshot_date"] = pd.to_datetime(
            frame["extra"].apply(lambda value: value.get("snapshot_date") or value.get("date")),
            errors="coerce",
        )
        frame = frame.dropna(subset=["contract_label", "curve_label", "value"])
        if frame.empty:
            return pd.DataFrame()

        latest_by_curve = frame.groupby("curve_label")["snapshot_date"].max().dropna()
        selected = []
        for curve_label, snapshot_date in latest_by_curve.items():
            subset = frame[
                (frame["curve_label"] == curve_label)
                & (frame["snapshot_date"] == snapshot_date)
            ]
            selected.append(subset)
        if not selected:
            return pd.DataFrame()
        plot_df = pd.concat(selected, ignore_index=True)
        plot_df = plot_df.rename(columns={"value": chart_cfg.get("y_field", "value")})
        return plot_df[["contract_label", "curve_label", chart_cfg.get("y_field", "value"), "snapshot_date"]]

    wide = (
        rows[["date", "series_name", "value"]]
        .drop_duplicates(subset=["date", "series_name"], keep="last")
        .pivot(index="date", columns="series_name", values="value")
        .reset_index()
        .sort_values("date")
    )
    wide.columns.name = None
    wide = _apply_chart_transforms(wide, indicator)
    wide = _apply_calculation(wide, indicator.calculation)
    y_field = chart_cfg.get("y_field", "value")
    value_columns = [column for column in wide.columns if column != "date"]
    if chart_type == "line" and y_field not in wide.columns and len(value_columns) == 1:
        wide = wide.rename(columns={value_columns[0]: y_field})

    applied_indicator = indicator
    if start_date or end_date:
        args = dict(indicator.arguments)
        if start_date:
            args["start_date"] = start_date
        if end_date:
            args["end_date"] = end_date
        applied_indicator = replace(indicator, arguments=args)
    return _apply_plot_date_window(wide, applied_indicator)


def build_indicator_dataset(indicator_id: str, start_date: str | None = None, end_date: str | None = None) -> tuple[Indicator, pd.DataFrame, pd.DataFrame]:
    indicator = get_indicator(indicator_id)
    plot_df = build_plot_dataframe(indicator, start_date=start_date, end_date=end_date)
    raw_df = load_observation_rows(indicator_id)
    if start_date:
        raw_df = raw_df[raw_df["date"] >= pd.Timestamp(start_date)]
    if end_date:
        raw_df = raw_df[raw_df["date"] <= pd.Timestamp(end_date)]

    chart_cfg = indicator.chart or {}
    chart_type = chart_cfg.get("type", "line")
    if chart_type == "stacked_area" and not raw_df.empty:
        visible_series = [field for field in chart_cfg.get("stack_fields", []) if field]
        y_field = chart_cfg.get("y_field")
        if y_field:
            visible_series.append(y_field)
        visible_series = list(dict.fromkeys(visible_series))
        raw_df = raw_df[raw_df["series_name"].isin(visible_series)]

    return indicator, plot_df.reset_index(drop=True), raw_df.reset_index(drop=True)


def load_dashboard_cards(theme_key: str | None = None) -> list[dict[str, Any]]:
    conn = _connect_read_only()
    chart_rows: dict[str, dict[str, Any]] = {}
    if conn is not None:
        try:
            try:
                for row in conn.execute(
                    "SELECT indicator_id, image_path, html_path, rendered_at, source_summary FROM charts"
                ).fetchall():
                    chart_rows[row[0]] = {
                        "image_path": row[1],
                        "html_path": row[2],
                        "rendered_at": row[3],
                        "source_summary": row[4],
                    }
            except duckdb.Error:
                chart_rows = {}
        finally:
            conn.close()

    records: list[dict[str, Any]] = []
    for indicator in load_indicators():
        if theme_key and indicator.theme != theme_key:
            continue
        dataset = build_plot_dataframe(indicator)
        raw_dataset = load_observation_rows(indicator.id)
        quality_ok, quality_reason = chart_data_quality(
            indicator,
            raw_df=raw_dataset,
            plot_df=dataset,
        )
        chart_cfg = indicator.chart or {}
        chart_type = chart_cfg.get("type", "line")
        latest_value = None
        latest_date = None
        if quality_ok and not dataset.empty and chart_type == "term_structure":
            current_field = chart_cfg.get("y_field", "value")
            current_slice = dataset.sort_values("snapshot_date")
            latest_value = current_slice[current_field].iloc[-1]
            latest_date = current_slice["snapshot_date"].iloc[-1]
        elif quality_ok and not dataset.empty and "date" in dataset.columns:
            y_field = chart_cfg.get("y_field", "value")
            target_field = y_field if y_field in dataset.columns else next(
                (column for column in dataset.columns if column != "date"),
                None,
            )
            if target_field:
                current_slice = dataset.dropna(subset=[target_field]).sort_values("date")
                if not current_slice.empty:
                    latest_value = current_slice[target_field].iloc[-1]
                    latest_date = current_slice["date"].iloc[-1]
        chart_meta = chart_rows.get(indicator.id, {})
        history_span_days = None
        if quality_ok and not dataset.empty:
            history_field = "snapshot_date" if chart_type == "term_structure" else "date"
            if history_field in dataset.columns:
                dates = pd.to_datetime(dataset[history_field], errors="coerce").dropna()
                if not dates.empty:
                    history_span_days = int((dates.max() - dates.min()).days)
        records.append(
            {
                "id": indicator.id,
                "theme": indicator.theme,
                "title": indicator.title,
                "description": indicator.description,
                "source": display_source_label(chart_meta.get("source_summary") or indicator.source),
                "latest_value": latest_value,
                "latest_date": latest_date,
                "updated_at": chart_meta.get("rendered_at"),
                "image_path": chart_meta.get("image_path") if quality_ok else None,
                "html_path": chart_meta.get("html_path") if quality_ok else None,
                "history_span_days": history_span_days,
                "chart_available": quality_ok and not dataset.empty,
                "chart_block_reason": quality_reason if not quality_ok else "",
            }
        )
    return records
