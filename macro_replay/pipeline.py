from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
import re
from typing import Any, Dict, List, Tuple

import pandas as pd

from datahub.adapters.manual_excel import (
    extract_manual_term_block,
    load_manual_line_series,
    load_manual_multi_line_series,
    load_manual_term_structure,
)
from .charts import render_chart
from .config import Indicator, load_indicators, load_mcp_servers, load_theme_settings
from .data_quality import chart_data_quality
from .db import earliest_observation_time, ensure_db, latest_observation_time, record_chart, record_manual_import, replace_observations_since, upsert_observations
from .source_labels import display_source_label
from .sources import fred, ifind

COMEX_MONTH_CODES = "FGHJKMNQUVXZ"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHART_CACHE_BUST_FILE = PROJECT_ROOT / "charts" / ".cache_bust"
DEFAULT_REVISION_LOOKBACK_DAYS = 30
DEFAULT_FETCH_START_DATE = "2000-01-01"


def touch_chart_cache_bust() -> None:
    CHART_CACHE_BUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHART_CACHE_BUST_FILE.write_text(str(pd.Timestamp.now().isoformat()), encoding="utf-8")


def combine_frames(frames: Dict[str, pd.DataFrame], how: str = "inner") -> pd.DataFrame:
    combined = None
    for _, df in frames.items():
        if combined is None:
            combined = df.copy()
        else:
            combined = combined.merge(df, on="date", how=how)
    return combined if combined is not None else pd.DataFrame()


def _apply_calculation(df: pd.DataFrame, calculation: str | None) -> pd.DataFrame:
    if not calculation:
        return df
    # Support multiple assignment expressions separated by ';' in config.
    for expr in [item.strip() for item in calculation.split(";") if item.strip()]:
        df.eval(expr, inplace=True)
    return df


def _apply_chart_transforms(df: pd.DataFrame, indicator: Indicator) -> pd.DataFrame:
    if df.empty:
        return df

    chart_cfg = indicator.chart or {}
    result = df.copy()

    if chart_cfg.get("resample_rule") and "date" in result.columns:
        result = (
            result.set_index("date")
            .sort_index()
            .resample(chart_cfg["resample_rule"])
            .last()
            .reset_index()
        )

    fill_forward_fields = chart_cfg.get("fill_forward_fields")
    if fill_forward_fields:
        for field in fill_forward_fields:
            if field in result.columns:
                result[field] = result[field].ffill()
    elif chart_cfg.get("fill_method") == "ffill":
        value_fields = [col for col in result.columns if col != "date"]
        if value_fields:
            result[value_fields] = result[value_fields].ffill()

    fillna_fields = chart_cfg.get("fillna_fields") or {}
    for field, value in fillna_fields.items():
        if field in result.columns:
            result[field] = result[field].fillna(value)

    return result


def _series_revision_lookback_days(indicator: Indicator, series_cfg: Dict[str, Any]) -> int:
    if "revision_lookback_days" in series_cfg:
        return max(0, int(series_cfg["revision_lookback_days"]))
    args = indicator.arguments or {}
    if "revision_lookback_days" in args:
        return max(0, int(args["revision_lookback_days"]))
    return DEFAULT_REVISION_LOOKBACK_DAYS


def _fetch_window(indicator: Indicator) -> tuple[str, str]:
    args = indicator.arguments or {}
    fetch_start = args.get("fetch_start_date") or DEFAULT_FETCH_START_DATE
    fetch_end = args.get("fetch_end_date") or date.today().isoformat()
    return str(fetch_start), str(fetch_end)


def _replace_series_rows(
    conn,
    indicator_id: str,
    series_id: str,
    source_id: str,
    rows: List[Tuple[Any, float, Dict[str, Any]]],
    unit: str | None = None,
) -> None:
    if not rows:
        return
    sorted_rows = sorted(rows, key=lambda item: item[0])
    replace_observations_since(
        conn,
        indicator_id,
        series_id,
        source_id,
        sorted_rows,
        since=sorted_rows[0][0],
        unit=unit,
    )


def load_series_frame_from_db(conn, indicator_id: str, series_id: str, value_name: str) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT obs_time, value
        FROM observations
        WHERE indicator_id = ? AND series_id = ?
        ORDER BY obs_time
        """,
        [indicator_id, series_id],
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["date", value_name])
    return pd.DataFrame(rows, columns=["date", value_name])


def _load_series_from_db(conn, indicator: Indicator, series_id: str, value_name: str) -> pd.DataFrame | None:
    df = load_series_frame_from_db(conn, indicator.id, series_id, value_name)
    if df.empty:
        return None
    return df


def _merge_series_frames(frames: List[pd.DataFrame]) -> pd.DataFrame:
    combined = None
    for frame in frames:
        if combined is None:
            combined = frame.copy()
        else:
            combined = combined.merge(frame, on="date", how="outer")
    return combined if combined is not None else pd.DataFrame()


def rerender_indicator_from_db(indicator: Indicator, conn) -> bool:
    chart_cfg = indicator.chart or {}
    chart_type = chart_cfg.get("type", "line")

    if indicator.source in {"manual-excel-line", "manual-excel-multi-line", "manual-excel-term"}:
        return False

    if chart_type == "term_structure":
        # Term-structure charts require curve reconstruction; skip here.
        return False

    if chart_type == "multi_axis_line":
        required_fields = [
            field
            for field in chart_cfg.get("left_fields", []) + chart_cfg.get("right_fields", [])
            if field
        ]
        if not required_fields:
            return False
        frames = []
        for field in required_fields:
            series_id = f"{indicator.id}:{field}"
            frame = _load_series_from_db(conn, indicator, series_id, field)
            if frame is not None:
                frames.append(frame)
        if not frames:
            return False
        plot_df = _merge_series_frames(frames)
        plot_df = plot_df[["date", *[f for f in required_fields if f in plot_df.columns]]].dropna(
            how="all", subset=[f for f in required_fields if f in plot_df.columns]
        )
    elif chart_type == "stacked_area":
        stack_fields = [field for field in chart_cfg.get("stack_fields", []) if field]
        extra_fields = [chart_cfg.get("y_field")] if chart_cfg.get("y_field") else []
        frames = []
        for field in dict.fromkeys(stack_fields + extra_fields):
            series_id = f"{indicator.id}:{field}"
            frame = _load_series_from_db(conn, indicator, series_id, field)
            if frame is not None:
                frames.append(frame)
        if not frames:
            return False
        plot_df = _merge_series_frames(frames)
        keep_fields = [field for field in dict.fromkeys(stack_fields + extra_fields) if field in plot_df.columns]
        if not keep_fields:
            return False
        subset_fields = [field for field in stack_fields if field in plot_df.columns]
        plot_df = plot_df[["date", *keep_fields]].dropna(how="all", subset=subset_fields)
    else:
        y_field = chart_cfg.get("y_field", "value")
        preferred_series = []
        preferred_series.append(f"{indicator.id}:{y_field}")
        preferred_series.append(indicator.id)
        preferred_series.extend(f"{indicator.id}:{series['code']}" for series in (indicator.series or []))
        plot_df = None
        for series_id in dict.fromkeys(preferred_series):
            frame = _load_series_from_db(conn, indicator, series_id, y_field)
            if frame is not None:
                plot_df = frame
                break
        if plot_df is None:
            row = conn.execute(
                """
                SELECT series_id
                FROM observations
                WHERE indicator_id = ?
                GROUP BY series_id
                ORDER BY count(*) DESC, max(obs_time) DESC
                LIMIT 1
                """,
                [indicator.id],
            ).fetchone()
            if row and row[0]:
                plot_df = _load_series_from_db(conn, indicator, row[0], y_field)
        if plot_df is None or plot_df.empty:
            return False

    plot_df = _apply_chart_transforms(plot_df, indicator)
    plot_df = _apply_plot_date_window(plot_df, indicator)
    if plot_df.empty:
        return False
    usable, _ = chart_data_quality(indicator, plot_df=plot_df)
    if not usable:
        return False

    html_path, image_path = render_chart(plot_df, indicator, Path("charts") / indicator.id)
    record_chart(conn, indicator.id, html_path, image_path, display_source_label(indicator.source))
    return True


def rerender_theme_from_db(theme_key: str, start_date: str, end_date: str) -> None:
    indicators = [item for item in load_indicators() if item.theme == theme_key]
    conn = ensure_db()
    try:
        for indicator in indicators:
            args = dict(indicator.arguments)
            args["start_date"] = start_date
            args["end_date"] = end_date
            themed_indicator = replace(indicator, arguments=args)
            ok = rerender_indicator_from_db(themed_indicator, conn)
            if not ok:
                if themed_indicator.source in {"manual-excel-line", "manual-excel-multi-line", "manual-excel-term"}:
                    process_indicator(themed_indicator, {})
                else:
                    print(f"[WARN] {indicator.id} skipped during rerender (no data or unsupported)")
    finally:
        conn.close()


def fetch_series_frame(
    series_cfg: Dict[str, Any],
    indicator: Indicator,
    start: str,
    end: str,
    servers: Dict[str, Any],
):
    source = series_cfg.get("source") or indicator.source
    code = series_cfg["code"]
    if source == "fred":
        df = fred.fetch_series(series_cfg["url"], code, start)
        unit = series_cfg.get("unit")
        multiplier = float(series_cfg.get("multiplier", 1.0))
        if multiplier != 1.0 and not df.empty:
            df[code] = df[code] * multiplier
        return df, unit
    if source == "ifind":
        if not indicator.server or not indicator.tool:
            raise ValueError(f"Indicator {indicator.id} missing server/tool config")
        if indicator.server not in servers:
            raise ValueError(f"Server {indicator.server} not found in config/mcp_servers.toml")
        df, unit = ifind.fetch_series_dataframe(
            server=servers[indicator.server],
            tool=indicator.tool,
            series_cfg=series_cfg,
            indicator_args=indicator.arguments,
            start=start,
            end=end,
            code=code,
        )
        if df.empty and series_cfg.get("fallback"):
            fallback_cfg = dict(series_cfg["fallback"])
            fallback_cfg.setdefault("code", code)
            df, unit = fetch_series_frame(fallback_cfg, indicator, start, end, servers)
        multiplier = float(series_cfg.get("multiplier", 1.0))
        if multiplier != 1.0 and not df.empty:
            df[code] = df[code] * multiplier
        return df, unit
    if source == "ifind-edb-table":
        if not indicator.server or not indicator.tool:
            raise ValueError(f"Indicator {indicator.id} missing server/tool config")
        if indicator.server not in servers:
            raise ValueError(f"Server {indicator.server} not found in config/mcp_servers.toml")
        query = (series_cfg.get("query") or "{code} {start_date}至{end_date}").format(
            code=code,
            start_date=start,
            end_date=end,
        )
        df, unit = ifind.fetch_edb_markdown_dataframe(
            server=servers[indicator.server],
            tool=series_cfg.get("tool") or indicator.tool,
            query=query,
            code=code,
            value_column=series_cfg.get("value_column", code),
            date_column=series_cfg.get("date_column", "日期"),
        )
        multiplier = float(series_cfg.get("multiplier", 1.0))
        if multiplier != 1.0 and not df.empty:
            df[code] = df[code] * multiplier
        if series_cfg.get("unit_override"):
            unit = series_cfg["unit_override"]
        return df, unit
    if source == "ifind-edb-direct":
        query = (series_cfg.get("query") or "{code} {start_date}至{end_date}").format(
            code=series_cfg.get("query_code", code),
            start_date=start,
            end_date=end,
        )
        df, unit = ifind.fetch_edb_direct_dataframe(
            query=query,
            code=code,
            value_column=series_cfg.get("value_column", code),
            comein_index_name=series_cfg.get('comein_index_name'),
        )
        multiplier = float(series_cfg.get("multiplier", 1.0))
        target_unit=series_cfg.get('unit_override')
        if not df.empty and target_unit in ('元','亿元'):
            scales={'元':1.0,'亿元':1e8,'万元':1e4}
            if unit not in scales:
                raise ValueError(f'Unverified EDB unit for {code}: {unit}')
            multiplier=scales[unit]/scales[target_unit]
        if multiplier != 1.0 and not df.empty:
            df[code] = df[code] * multiplier
        if series_cfg.get("unit_override"):
            unit = series_cfg["unit_override"]
        return df, unit
    if source == "ifind-stock":
        if not indicator.server or not indicator.tool:
            raise ValueError(f"Indicator {indicator.id} missing server/tool config")
        if indicator.server not in servers:
            raise ValueError(f"Server {indicator.server} not found in config/mcp_servers.toml")
        query = (series_cfg.get("query") or "{code} {start_date}至{end_date} 收盘价").format(
            code=code,
            start_date=start,
            end_date=end,
        )
        history = ifind.fetch_stock_history_dataframe(
            server=servers[indicator.server],
            tool=series_cfg.get("tool") or indicator.tool,
            query=query,
            value_keyword=series_cfg.get("value_keyword", "收盘价"),
        )
        if history.empty:
            return pd.DataFrame(columns=["date", code]), series_cfg.get("unit")
        history = history.rename(columns={"value": code})[["date", code]]
        return history, series_cfg.get("unit")
    raise NotImplementedError(f"Source {source} not implemented")


def _month_start(value: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=value.year, month=value.month, day=1)


def _add_months(base: pd.Timestamp, offset: int) -> pd.Timestamp:
    return _month_start(base) + pd.DateOffset(months=offset)


def _build_shfe_contract_codes(anchor: str, prefix: str, suffix: str, months_ahead: int) -> List[str]:
    anchor_ts = _month_start(pd.Timestamp(anchor))
    codes: List[str] = []
    for offset in range(months_ahead + 1):
        contract_month = _add_months(anchor_ts, offset)
        codes.append(f"{prefix}{contract_month.strftime('%y%m')}.{suffix}".upper())
    return codes


def _build_comex_contract_codes(anchor: str, prefix: str, suffix: str, months_ahead: int) -> List[str]:
    anchor_ts = _month_start(pd.Timestamp(anchor))
    codes: List[str] = []
    for offset in range(months_ahead + 1):
        contract_month = _add_months(anchor_ts, offset)
        month_code = COMEX_MONTH_CODES[contract_month.month - 1]
        year_code = contract_month.strftime("%y")
        codes.append(f"{prefix}{year_code}{month_code}{suffix}".upper())
    return codes


def _extract_delivery_info(code: str, pattern: str, anchor: pd.Timestamp) -> tuple[pd.Timestamp, str]:
    match = re.match(pattern, code)
    if not match:
        raise ValueError(f"Unable to parse delivery month from contract code: {code}")
    groups = match.groupdict()
    if groups.get("month_code"):
        month = COMEX_MONTH_CODES.index(match.group("month_code")) + 1
        year = 2000 + int(match.group("yy"))
    elif groups.get("yy"):
        year = 2000 + int(match.group("yy"))
        month = int(match.group("mm"))
    else:
        month = int(match.group("mm"))
        year = anchor.year + (1 if month < anchor.month else 0)
    delivery = pd.Timestamp(year=year, month=month, day=1)
    return delivery, delivery.strftime("%y%m")


def _build_term_structure_frame(history: pd.DataFrame, indicator: Indicator) -> tuple[pd.DataFrame, pd.DataFrame]:
    chart_cfg = indicator.chart or {}
    args = indicator.arguments or {}
    anchor = pd.Timestamp(args.get("end_date") or date.today().isoformat())
    delivery_pattern = args.get("delivery_pattern", r"^[A-Z]+(?P<yy>\d{2})(?P<mm>\d{2})\.[A-Z]+$")
    snapshot_defs = [
        ("今日", anchor.normalize()),
        ("1日前", (anchor - pd.Timedelta(days=1)).normalize()),
        ("1周前", (anchor - pd.Timedelta(days=7)).normalize()),
        ("1月前", (anchor - pd.DateOffset(months=1)).normalize()),
        ("半年前", (anchor - pd.DateOffset(months=6)).normalize()),
    ]

    current_slice = history[history["date"] <= anchor].copy()
    current_slice = current_slice[current_slice["date"] >= (anchor - pd.Timedelta(days=7)).normalize()]
    if current_slice.empty:
        return pd.DataFrame(), pd.DataFrame()

    latest_market_date = current_slice["date"].max()
    active_codes = set(current_slice.loc[current_slice["date"] == latest_market_date, "code"])
    if not active_codes:
        return pd.DataFrame(), pd.DataFrame()

    history = history[history["code"].isin(active_codes)].copy()
    delivery_rows = []
    for code in sorted(active_codes):
        delivery, label = _extract_delivery_info(code, delivery_pattern, anchor)
        delivery_rows.append({"code": code, "delivery": delivery, "contract_label": label})
    delivery_map = pd.DataFrame(delivery_rows)
    history = history.merge(delivery_map, on="code", how="left")

    curve_rows: List[Dict[str, Any]] = []
    raw_rows: List[Dict[str, Any]] = []
    for label, target_date in snapshot_defs:
        available = history[history["date"] <= target_date]
        if available.empty:
            continue
        snapshot_date = available["date"].max()
        snapshot = history[history["date"] == snapshot_date].copy()
        if snapshot.empty:
            continue
        snapshot["curve_label"] = label
        snapshot["snapshot_date"] = snapshot_date
        curve_rows.extend(snapshot.to_dict("records"))
        raw_rows.extend(snapshot.to_dict("records"))

    curve_df = pd.DataFrame(curve_rows)
    if curve_df.empty:
        return pd.DataFrame(), pd.DataFrame()
    ordered_labels = delivery_map.sort_values("delivery")["contract_label"].tolist()
    curve_df = curve_df.sort_values(["delivery", "snapshot_date"])
    curve_df["contract_label"] = pd.Categorical(
        curve_df["contract_label"],
        categories=ordered_labels,
        ordered=True,
    )
    curve_df = curve_df.rename(columns={"value": chart_cfg.get("y_field", "value")})
    raw_df = pd.DataFrame(raw_rows).sort_values(["snapshot_date", "delivery"])
    return curve_df, raw_df


def _apply_plot_date_window(df: pd.DataFrame, indicator: Indicator) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return df

    args = indicator.arguments or {}
    start = args.get("start_date")
    end = args.get("end_date")
    if not start and not end:
        return df

    result = df.copy()
    data_start = result["date"].min()
    data_end = result["date"].max()
    start_ts = pd.Timestamp(start) if start else None
    end_ts = pd.Timestamp(end) if end else None

    if start_ts is not None and pd.notna(data_start) and data_start > start_ts:
        start_ts = data_start
    if end_ts is not None and pd.notna(data_end) and data_end < end_ts:
        end_ts = data_end

    if start_ts is not None:
        result = result[result["date"] >= start_ts]
    if end_ts is not None:
        result = result[result["date"] <= end_ts]
    return result.reset_index(drop=True)


def _manual_import_file_mtime(workbook_relative_path: str) -> pd.Timestamp | None:
    workbook_path = PROJECT_ROOT / workbook_relative_path
    if not workbook_path.exists():
        return None
    return pd.Timestamp(workbook_path.stat().st_mtime, unit="s")


def process_manual_term_structure_indicator(indicator: Indicator, conn) -> None:
    load_result = load_manual_term_structure(indicator, PROJECT_ROOT)
    curve_df = load_result.curve_frame
    raw_df = load_result.raw_frame
    if curve_df.empty or raw_df.empty:
        record_manual_import(
            conn,
            indicator.id,
            indicator.source,
            load_result.file_path,
            load_result.sheet_name,
            _manual_import_file_mtime(load_result.file_path),
            "replace_since",
            load_result.rows_read,
            0,
            None,
            None,
            "empty",
            note=str((indicator.arguments or {}).get("note", "")),
            meta={"chart_type": "term_structure"},
        )
        return

    y_field = (indicator.chart or {}).get("y_field", "value")
    curve_df = curve_df.rename(columns={"value": y_field})
    rows_written = 0

    for contract, group in raw_df.groupby("contract_label"):
        rows = []
        for _, row in group.iterrows():
            snapshot_date = row["snapshot_date"]
            if pd.isna(snapshot_date):
                continue
            extra = {
                "curve_label": row.get("curve_label"),
                "snapshot_date": pd.Timestamp(snapshot_date).strftime("%Y-%m-%d"),
                "contract_label": contract,
            }
            rows.append((pd.Timestamp(snapshot_date).to_pydatetime(), row["value"], extra))
        if rows:
            rows_written += len(rows)
            _replace_series_rows(
                conn,
                indicator.id,
                f"{indicator.id}:{contract}",
                indicator.source,
                rows,
                unit=(indicator.chart or {}).get("unit"),
            )

    plot_df = curve_df[["contract_label", "curve_label", y_field]].dropna()
    html_path, image_path = render_chart(plot_df, indicator, Path("charts") / indicator.id)
    record_chart(conn, indicator.id, html_path, image_path, display_source_label(indicator.source))
    record_manual_import(
        conn,
        indicator.id,
        indicator.source,
        load_result.file_path,
        load_result.sheet_name,
        _manual_import_file_mtime(load_result.file_path),
        "replace_since",
        load_result.rows_read,
        rows_written,
        raw_df["snapshot_date"].min() if "snapshot_date" in raw_df.columns else None,
        raw_df["snapshot_date"].max() if "snapshot_date" in raw_df.columns else None,
        "success",
        note=str((indicator.arguments or {}).get("note", "")),
        meta={"chart_type": "term_structure", "contracts": sorted(raw_df["contract_label"].unique().tolist())},
    )


def process_manual_line_indicator(indicator: Indicator, conn) -> None:
    load_result = load_manual_line_series(indicator, PROJECT_ROOT)
    df = load_result.frame
    if df.empty:
        record_manual_import(
            conn,
            indicator.id,
            indicator.source,
            load_result.file_path,
            load_result.sheet_name,
            _manual_import_file_mtime(load_result.file_path),
            "replace_since",
            load_result.rows_read,
            0,
            None,
            None,
            "empty",
            note=str((indicator.arguments or {}).get("note", "")),
            meta={"chart_type": "line"},
        )
        return

    y_field = (indicator.chart or {}).get("y_field", "value")
    full_df = df.rename(columns={"value": y_field})
    rows = [(row["date"].to_pydatetime(), row[y_field], {}) for _, row in full_df.iterrows()]
    _replace_series_rows(
        conn,
        indicator.id,
        f"{indicator.id}:{y_field}",
        indicator.source,
        rows,
        unit=(indicator.chart or {}).get("unit"),
    )

    plot_df = full_df
    plot_df = _apply_plot_date_window(plot_df, indicator)
    if plot_df.empty:
        record_manual_import(
            conn,
            indicator.id,
            indicator.source,
            load_result.file_path,
            load_result.sheet_name,
            _manual_import_file_mtime(load_result.file_path),
            "replace_since",
            load_result.rows_read,
            len(rows),
            None,
            None,
            "empty",
            note=str((indicator.arguments or {}).get("note", "")),
            meta={"chart_type": "line", "reason": "filtered_by_window"},
        )
        return

    html_path, image_path = render_chart(plot_df, indicator, Path("charts") / indicator.id)
    record_chart(conn, indicator.id, html_path, image_path, display_source_label(indicator.source))
    record_manual_import(
        conn,
        indicator.id,
        indicator.source,
        load_result.file_path,
        load_result.sheet_name,
        _manual_import_file_mtime(load_result.file_path),
        "replace_since",
        load_result.rows_read,
        len(rows),
        plot_df["date"].min(),
        plot_df["date"].max(),
        "success",
        note=str((indicator.arguments or {}).get("note", "")),
        meta={"chart_type": "line", "y_field": y_field},
    )


def process_manual_multi_line_indicator(indicator: Indicator, conn) -> None:
    load_result = load_manual_multi_line_series(indicator, PROJECT_ROOT)
    df = load_result.frame
    if df.empty:
        record_manual_import(
            conn,
            indicator.id,
            indicator.source,
            load_result.file_path,
            load_result.sheet_name,
            _manual_import_file_mtime(load_result.file_path),
            "replace_since",
            load_result.rows_read,
            0,
            None,
            None,
            "empty",
            note=str((indicator.arguments or {}).get("note", "")),
            meta={"chart_type": (indicator.chart or {}).get("type", "line")},
        )
        return

    chart_cfg = indicator.chart or {}
    chart_type = chart_cfg.get("type", "line")
    working = df.copy()
    working = _apply_calculation(working, indicator.calculation)

    fields_to_store = list(dict.fromkeys(
        [
            *[item["field"] for item in (indicator.arguments or {}).get("series_columns", [])],
            *chart_cfg.get("left_fields", []),
            *chart_cfg.get("right_fields", []),
            *(chart_cfg.get("derived_fields") or []),
            chart_cfg.get("y_field", "value"),
        ]
    ))
    fields_to_store = [field for field in fields_to_store if field and field in working.columns]

    field_units = chart_cfg.get("field_units", {})
    default_unit = chart_cfg.get("unit")
    rows_written = 0
    for field in fields_to_store:
        series_rows = [
            (row["date"].to_pydatetime(), row[field], {})
            for _, row in working[["date", field]].dropna().iterrows()
        ]
        if series_rows:
            rows_written += len(series_rows)
            _replace_series_rows(
                conn,
                indicator.id,
                f"{indicator.id}:{field}",
                indicator.source,
                series_rows,
                unit=field_units.get(field, default_unit),
            )

    if chart_type == "multi_axis_line":
        required_fields = [
            field
            for field in chart_cfg.get("left_fields", []) + chart_cfg.get("right_fields", [])
            if field in working.columns
        ]
        plot_df = working[["date", *required_fields]].dropna(how="all", subset=required_fields)
    else:
        y_field = chart_cfg.get("y_field", "value")
        if y_field not in working.columns:
            return
        plot_df = working[["date", y_field]].dropna()

    plot_df = _apply_plot_date_window(plot_df, indicator)
    if plot_df.empty:
        record_manual_import(
            conn,
            indicator.id,
            indicator.source,
            load_result.file_path,
            load_result.sheet_name,
            _manual_import_file_mtime(load_result.file_path),
            "replace_since",
            load_result.rows_read,
            0,
            None,
            None,
            "empty",
            note=str((indicator.arguments or {}).get("note", "")),
            meta={"chart_type": chart_type, "reason": "filtered_by_window"},
        )
        return

    html_path, image_path = render_chart(plot_df, indicator, Path("charts") / indicator.id)
    record_chart(conn, indicator.id, html_path, image_path, display_source_label(indicator.source))
    record_manual_import(
        conn,
        indicator.id,
        indicator.source,
        load_result.file_path,
        load_result.sheet_name,
        _manual_import_file_mtime(load_result.file_path),
        "replace_since",
        load_result.rows_read,
        rows_written,
        plot_df["date"].min() if "date" in plot_df.columns else None,
        plot_df["date"].max() if "date" in plot_df.columns else None,
        "success",
        note=str((indicator.arguments or {}).get("note", "")),
        meta={"chart_type": chart_type, "fields": fields_to_store},
    )


def process_term_structure_indicator(indicator: Indicator, servers: Dict[str, Any], conn) -> None:
    if indicator.server not in servers:
        raise ValueError(f"Server {indicator.server} not found in config/mcp_servers.toml")

    args = indicator.arguments or {}
    market = args.get("market")
    if market not in {"shfe", "comex"}:
        raise NotImplementedError("Only SHFE and COMEX term structure are implemented for now")

    _, end_date = _fetch_window(indicator)
    anchor = pd.Timestamp(end_date)
    lookback_start = str(args.get("fetch_start_date") or (anchor - pd.DateOffset(months=6) - pd.Timedelta(days=7)).date().isoformat())

    if market == "shfe":
        contract_codes = _build_shfe_contract_codes(
            anchor=end_date,
            prefix=args.get("contract_prefix", "CU"),
            suffix=args.get("exchange_suffix", "SHF"),
            months_ahead=int(args.get("months_ahead", 18)),
        )
    else:
        contract_codes = _build_comex_contract_codes(
            anchor=end_date,
            prefix=args.get("contract_prefix", "@HG"),
            suffix=args.get("exchange_suffix", ".CMX"),
            months_ahead=int(args.get("months_ahead", 11)),
        )

    history_frames: List[pd.DataFrame] = []
    chunk_size = int(args.get("chunk_size", 3))
    for idx in range(0, len(contract_codes), chunk_size):
        codes = contract_codes[idx : idx + chunk_size]
        query = f"{'、'.join(codes)} {lookback_start}至{end_date} 收盘价"
        frame = ifind.fetch_stock_history_dataframe(
            server=servers[indicator.server],
            tool=indicator.tool,
            query=query,
        )
        if not frame.empty:
            history_frames.append(frame)

    if not history_frames:
        return

    history = pd.concat(history_frames, ignore_index=True)
    history = history.drop_duplicates(subset=["code", "date"], keep="last")
    curve_df, raw_df = _build_term_structure_frame(history, indicator)
    if curve_df.empty or raw_df.empty:
        return

    y_field = (indicator.chart or {}).get("y_field", "value")
    for code, group in raw_df.groupby("code"):
        rows = []
        for _, row in group.iterrows():
            extra = {
                "contract_name": row.get("name"),
                "curve_label": row.get("curve_label"),
                "snapshot_date": row.get("snapshot_date").strftime("%Y-%m-%d"),
                "contract_label": row.get("contract_label"),
            }
            rows.append((row["snapshot_date"].to_pydatetime(), row["value"], extra))
        _replace_series_rows(
            conn,
            indicator.id,
            f"{indicator.id}:{code}",
            indicator.source,
            rows,
            unit=(indicator.chart or {}).get("unit"),
        )

    plot_df = curve_df[["contract_label", "curve_label", y_field]].dropna()
    html_path, image_path = render_chart(plot_df, indicator, Path("charts") / indicator.id)
    record_chart(conn, indicator.id, html_path, image_path, display_source_label(indicator.source))


def process_indicator(indicator: Indicator, servers: Dict[str, Any]) -> None:
    print(f"[INFO] Processing {indicator.id} ({indicator.source})")
    conn = ensure_db()
    try:
        chart_type = (indicator.chart or {}).get("type")
        if indicator.source == "manual-excel-term":
            process_manual_term_structure_indicator(indicator, conn)
            return
        if indicator.source == "manual-excel-line":
            process_manual_line_indicator(indicator, conn)
            return
        if indicator.source == "manual-excel-multi-line":
            process_manual_multi_line_indicator(indicator, conn)
            return
        if chart_type == "term_structure":
            process_term_structure_indicator(indicator, servers, conn)
            return

        fetch_window_start, fetch_window_end = _fetch_window(indicator)
        display_end = indicator.arguments.get("end_date") or fetch_window_end
        series_frames: Dict[str, pd.DataFrame] = {}

        if not indicator.series:
            return

        for series_cfg in indicator.series:
            series_id = f"{indicator.id}:{series_cfg['code']}"
            latest_time = latest_observation_time(conn, indicator.id, series_id)
            earliest_time = earliest_observation_time(conn, indicator.id, series_id)
            fetch_start = fetch_window_start
            lookback_days = _series_revision_lookback_days(indicator, series_cfg)
            replacement_start = None
            backfill_start = None
            if earliest_time is not None and pd.Timestamp(earliest_time) > pd.Timestamp(fetch_window_start):
                backfill_start = pd.Timestamp(fetch_window_start)
            elif latest_time is not None and lookback_days <= 0:
                fetch_start = max(pd.Timestamp(fetch_window_start), pd.Timestamp(latest_time) + pd.Timedelta(days=1)).date().isoformat()
                # If database already covers the requested end date, skip fetching.
                if pd.Timestamp(latest_time) >= pd.Timestamp(fetch_window_end):
                    series_frames[series_cfg["code"]] = load_series_frame_from_db(
                        conn, indicator.id, series_id, series_cfg["code"]
                    )
                    continue
            elif latest_time is not None:
                replacement_start = max(
                    pd.Timestamp(fetch_window_start),
                    pd.Timestamp(latest_time).normalize() - pd.Timedelta(days=lookback_days),
                )
                fetch_start = replacement_start.date().isoformat()

            if backfill_start is not None:
                fetch_start = backfill_start.date().isoformat()

            detected_unit = None
            source_label = series_cfg.get("source") or indicator.source
            try:
                df, detected_unit = fetch_series_frame(series_cfg, indicator, fetch_start, fetch_window_end, servers)
                source_label=df.attrs.get('source_id',source_label)
                if df.attrs.get('raw_payload') is not None:
                    import json
                    from datahub.sync.storage import start_run,finish_run
                    audit_run=start_run(conn,source_label,{'indicator':indicator.id,'start':fetch_start,'end':fetch_window_end,'attempts':df.attrs.get('source_attempts',[])})
                    conn.execute("INSERT INTO raw.acquisition_batches(run_id,source_id,symbol,request,records,representation) VALUES (?,?,?,?,?,'mcp_json')",
                                 [audit_run,source_label,indicator.id,json.dumps({'start':fetch_start,'end':fetch_window_end}),json.dumps(df.attrs['raw_payload'],ensure_ascii=False)])
                    finish_run(conn,audit_run,read=len(df))
                unit = series_cfg.get("unit") or detected_unit
                if not df.empty:
                    rows = [(row["date"].to_pydatetime(), row[series_cfg["code"]], {}) for _, row in df.iterrows()]
                    if backfill_start is not None:
                        if earliest_time is not None and pd.to_datetime(df["date"]).min() <= pd.Timestamp(earliest_time):
                            replace_observations_since(
                                conn,
                                indicator.id,
                                series_id,
                                source_label,
                                rows,
                                since=backfill_start.to_pydatetime(),
                                unit=unit,
                            )
                        else:
                            upsert_observations(conn, indicator.id, series_id, source_label, rows, unit=unit)
                    elif replacement_start is not None:
                        replace_observations_since(
                            conn,
                            indicator.id,
                            series_id,
                            source_label,
                            rows,
                            since=replacement_start.to_pydatetime(),
                            unit=unit,
                        )
                    else:
                        upsert_observations(conn, indicator.id, series_id, source_label, rows, unit=unit)
            except Exception as exc:
                cached_df = load_series_frame_from_db(conn, indicator.id, series_id, series_cfg["code"])
                if cached_df.empty:
                    raise
                print(
                    f"[WARN] {indicator.id}:{series_cfg['code']} refresh failed, falling back to cached data: {exc}"
                )
                raise RuntimeError(
                    f"{indicator.id}:{series_cfg['code']} refresh failed; cached data exists but update did not complete: {exc}"
                ) from exc

            series_frames[series_cfg["code"]] = load_series_frame_from_db(conn, indicator.id, series_id, series_cfg["code"])

        chart_cfg = indicator.chart or {}
        merge_how = chart_cfg.get("merge_how", "inner")
        combined = combine_frames(series_frames, how=merge_how)
        if combined.empty:
            return

        combined = _apply_chart_transforms(combined, indicator)
        y_field = chart_cfg.get("y_field", list(series_frames.keys())[0])
        combined = _apply_calculation(combined, indicator.calculation)
        if y_field not in combined.columns:
            y_field = list(series_frames.keys())[0]

        if chart_type == "multi_axis_line":
            required_fields = [field for field in chart_cfg.get("left_fields", []) + chart_cfg.get("right_fields", []) if field in combined.columns]
            plot_df = combined[["date", *required_fields]].dropna(how="all", subset=required_fields)
        elif chart_type == "stacked_area":
            stack_fields = [field for field in chart_cfg.get("stack_fields", []) if field in combined.columns]
            keep_fields = [field for field in dict.fromkeys(stack_fields + [y_field]) if field in combined.columns]
            if not stack_fields:
                return
            plot_df = combined[["date", *keep_fields]].dropna(how="all", subset=stack_fields)
        else:
            plot_df = combined[["date", y_field]].dropna()
        plot_df = _apply_plot_date_window(plot_df, indicator)
        window_months = chart_cfg.get("window_months")
        if window_months:
            cutoff = pd.Timestamp(display_end) - pd.DateOffset(months=int(window_months))
            plot_df = plot_df[plot_df["date"] >= cutoff]
        if plot_df.empty:
            return

        usable, _ = chart_data_quality(indicator, plot_df=plot_df)
        if not usable:
            return

        derived_fields = chart_cfg.get("derived_fields") or []
        if chart_type == "stacked_area":
            derived_fields = derived_fields or list(dict.fromkeys((chart_cfg.get("stack_fields") or []) + [y_field]))
        elif chart_type != "multi_axis_line":
            derived_fields = derived_fields or [y_field]
        for field in derived_fields:
            if field not in plot_df.columns:
                continue
            derived_rows = [(row["date"].to_pydatetime(), row[field], {}) for _, row in plot_df[["date", field]].dropna().iterrows()]
            _replace_series_rows(
                conn,
                indicator.id,
                f"{indicator.id}:{field}",
                indicator.source,
                derived_rows,
                unit=chart_cfg.get("field_units", {}).get(field, chart_cfg.get("unit")),
            )

        html_path, image_path = render_chart(plot_df, indicator, Path("charts") / indicator.id)
        record_chart(conn, indicator.id, html_path, image_path, display_source_label(indicator.source))
    finally:
        conn.close()


def process_theme(theme_key: str, start_date: str, end_date: str) -> None:
    indicators = [item for item in load_indicators() if item.theme == theme_key]
    try:
        servers = load_mcp_servers()
    except FileNotFoundError:
        servers = {}

    for indicator in indicators:
        args = dict(indicator.arguments)
        args["start_date"] = start_date
        args["end_date"] = end_date
        themed_indicator = replace(indicator, arguments=args)
        try:
            process_indicator(themed_indicator, servers)
        except Exception as exc:
            print(f"[WARN] {themed_indicator.id} failed during theme refresh: {exc}")


def process_all() -> dict:
    indicators = load_indicators()
    report = {"success": [], "failed": []}
    try:
        servers = load_mcp_servers()
    except FileNotFoundError:
        servers = {}
    for indicator in indicators:
        try:
            process_indicator(indicator, servers)
            report["success"].append({"indicator_id": indicator.id})
        except Exception as exc:
            report["failed"].append({"indicator_id": indicator.id, "error": str(exc)})
            print(f"[WARN] {indicator.id} failed: {exc}")
    return report
