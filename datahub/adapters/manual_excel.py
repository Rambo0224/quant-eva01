from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.utils import column_index_from_string

from macro_replay.config import Indicator


@dataclass(frozen=True)
class ManualExcelLoadResult:
    frame: pd.DataFrame
    file_path: str
    sheet_name: str
    rows_read: int


@dataclass(frozen=True)
class ManualTermStructureLoadResult:
    curve_frame: pd.DataFrame
    raw_frame: pd.DataFrame
    file_path: str
    sheet_name: str
    rows_read: int


def resolve_excel_col(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return column_index_from_string(text)


def parse_manual_date(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return pd.Timestamp(value).normalize()

    numeric = pd.to_numeric(value, errors="coerce")
    if not pd.isna(numeric) and float(numeric) > 1000:
        return pd.to_datetime(float(numeric), unit="D", origin="1899-12-30").normalize()

    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).normalize()


def parse_manual_value(value: Any) -> float | None:
    text = str(value).strip()
    if not text or text.lower() in {"nan", "fetching..."}:
        return None
    text = text.replace(",", "")
    try:
        numeric = float(text)
    except ValueError:
        return None
    if numeric == 0:
        return None
    return numeric


def _resolve_workbook_path(indicator: Indicator, project_root: Path) -> tuple[Path, str, str]:
    args = indicator.arguments or {}
    workbook_relative = str(args["workbook_path"])
    workbook_path = project_root / workbook_relative
    sheet_name = str(args["sheet_name"])
    return workbook_path, workbook_relative, sheet_name


def load_manual_line_series(indicator: Indicator, project_root: Path) -> ManualExcelLoadResult:
    args = indicator.arguments or {}
    workbook_path, workbook_relative, sheet_name = _resolve_workbook_path(indicator, project_root)
    date_col = resolve_excel_col(args["date_col"])
    value_col = resolve_excel_col(args["value_col"])
    data_start_row = int(args.get("data_start_row", 1))
    max_blank_rows = int(args.get("max_blank_rows", 5))

    df = pd.read_excel(workbook_path, sheet_name=sheet_name, header=None)
    rows: list[dict[str, Any]] = []
    blank_rows = 0

    for row_idx in range(data_start_row - 1, len(df)):
        raw_date = df.iat[row_idx, date_col - 1] if date_col - 1 < df.shape[1] else None
        raw_value = df.iat[row_idx, value_col - 1] if value_col - 1 < df.shape[1] else None

        ts = parse_manual_date(raw_date)
        value = pd.to_numeric(raw_value, errors="coerce")
        if ts is None or pd.isna(value):
            blank_rows += 1
            if rows and blank_rows >= max_blank_rows:
                break
            continue

        blank_rows = 0
        rows.append({"date": ts, "value": float(value)})

    if not rows:
        frame = pd.DataFrame(columns=["date", "value"])
    else:
        frame = pd.DataFrame(rows).drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    return ManualExcelLoadResult(
        frame=frame,
        file_path=workbook_relative,
        sheet_name=sheet_name,
        rows_read=len(rows),
    )


def load_manual_multi_line_series(indicator: Indicator, project_root: Path) -> ManualExcelLoadResult:
    args = indicator.arguments or {}
    workbook_path, workbook_relative, sheet_name = _resolve_workbook_path(indicator, project_root)
    date_col = resolve_excel_col(args["date_col"])
    series_columns = args.get("series_columns") or []
    data_start_row = int(args.get("data_start_row", 1))
    max_blank_rows = int(args.get("max_blank_rows", 5))

    if not series_columns:
        return ManualExcelLoadResult(
            frame=pd.DataFrame(columns=["date"]),
            file_path=workbook_relative,
            sheet_name=sheet_name,
            rows_read=0,
        )

    df = pd.read_excel(workbook_path, sheet_name=sheet_name, header=None)
    rows: list[dict[str, Any]] = []
    blank_rows = 0
    resolved_columns = [{"field": item["field"], "col": resolve_excel_col(item["col"])} for item in series_columns]

    for row_idx in range(data_start_row - 1, len(df)):
        raw_date = df.iat[row_idx, date_col - 1] if date_col - 1 < df.shape[1] else None
        ts = parse_manual_date(raw_date)
        if ts is None:
            blank_rows += 1
            if rows and blank_rows >= max_blank_rows:
                break
            continue

        row_data: dict[str, Any] = {"date": ts}
        has_value = False
        for item in resolved_columns:
            col_idx = item["col"] - 1
            raw_value = df.iat[row_idx, col_idx] if col_idx < df.shape[1] else None
            value = pd.to_numeric(raw_value, errors="coerce")
            if pd.isna(value):
                row_data[item["field"]] = None
                continue
            row_data[item["field"]] = float(value)
            has_value = True

        if not has_value:
            blank_rows += 1
            if rows and blank_rows >= max_blank_rows:
                break
            continue

        blank_rows = 0
        rows.append(row_data)

    if not rows:
        frame = pd.DataFrame(columns=["date", *[item["field"] for item in resolved_columns]])
    else:
        frame = pd.DataFrame(rows).drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    return ManualExcelLoadResult(
        frame=frame,
        file_path=workbook_relative,
        sheet_name=sheet_name,
        rows_read=len(rows),
    )


def load_manual_term_structure_sheet(workbook_path: Path, sheet_name: str) -> pd.DataFrame:
    return pd.read_excel(workbook_path, sheet_name=sheet_name, header=None)


def extract_manual_term_block(sheet_df: pd.DataFrame, market_name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    header_row = None
    code_col = None
    for row_idx in range(min(len(sheet_df), 5)):
        for col_idx in range(sheet_df.shape[1]):
            if str(sheet_df.iat[row_idx, col_idx]).strip() == market_name:
                header_row = row_idx
                code_col = col_idx
                break
        if header_row is not None:
            break
    if header_row is None or code_col is None:
        return pd.DataFrame(), pd.DataFrame()

    curve_labels: list[str] = []
    snapshot_dates: list[pd.Timestamp] = []
    for offset in range(1, 6):
        curve_labels.append(str(sheet_df.iat[header_row - 1, code_col + offset]).strip())
        snapshot_dates.append(pd.to_datetime(sheet_df.iat[header_row, code_col + offset], errors="coerce"))

    records: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    row_idx = header_row + 1
    while row_idx < len(sheet_df):
        contract = str(sheet_df.iat[row_idx, code_col]).strip()
        if not contract or contract.lower() == "nan":
            break
        has_any_value = False
        for offset, (curve_label, snapshot_date) in enumerate(zip(curve_labels, snapshot_dates), start=1):
            value = parse_manual_value(sheet_df.iat[row_idx, code_col + offset])
            if value is None:
                continue
            has_any_value = True
            record = {
                "contract_label": contract,
                "curve_label": curve_label,
                "snapshot_date": snapshot_date,
                "value": value,
            }
            records.append(record)
            raw_rows.append(record.copy())
        row_idx += 1

    if not records:
        return pd.DataFrame(), pd.DataFrame()

    curve_df = pd.DataFrame(records)
    ordered_labels = list(dict.fromkeys(curve_df["contract_label"].tolist()))
    curve_df["contract_label"] = pd.Categorical(curve_df["contract_label"], categories=ordered_labels, ordered=True)
    curve_df = curve_df.sort_values(["contract_label", "snapshot_date"])
    raw_df = pd.DataFrame(raw_rows).sort_values(["snapshot_date", "contract_label"])
    return curve_df, raw_df


def load_manual_term_structure(indicator: Indicator, project_root: Path) -> ManualTermStructureLoadResult:
    args = indicator.arguments or {}
    workbook_path, workbook_relative, sheet_name = _resolve_workbook_path(indicator, project_root)
    market_name = str(args["market_name"])
    sheet_df = load_manual_term_structure_sheet(workbook_path, sheet_name)
    curve_df, raw_df = extract_manual_term_block(sheet_df, market_name)
    return ManualTermStructureLoadResult(
        curve_frame=curve_df,
        raw_frame=raw_df,
        file_path=workbook_relative,
        sheet_name=sheet_name,
        rows_read=len(raw_df),
    )
