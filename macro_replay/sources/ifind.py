from __future__ import annotations

from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Tuple

import pandas as pd

from ..config import MCPServer
from ..mcp_client import MCPClient


DEFAULT_IFIND_SKILL_DIR = Path(r"C:\Users\heshu\.codex\skills\ifind-finance-data")


def _load_ifind_skill_call_module():
    skill_dir = Path(os.environ.get("IFIND_FINANCE_DATA_SKILL_DIR", DEFAULT_IFIND_SKILL_DIR))
    module_path = skill_dir / "call.py"
    if not module_path.exists():
        raise FileNotFoundError(f"iFinD skill call module not found: {module_path}")
    spec = importlib.util.spec_from_file_location("omnisignal_ifind_skill_call", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load iFinD skill call module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def format_arguments(template: Dict[str, Any] | None, start: str, end: str) -> Dict[str, Any]:
    formatted: Dict[str, Any] = {}
    for key, value in (template or {}).items():
        if isinstance(value, str):
            formatted[key] = value.format(start_date=start, end_date=end)
        else:
            formatted[key] = value
    return formatted


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_line_series(response: Dict[str, Any]) -> Tuple[List[tuple], str | None]:
    content = response.get("result", {}).get("content", [])
    series: List[tuple] = []
    unit: str | None = None
    for block in content:
        text = block.get("text")
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        data_section = payload.get("data") or {}
        datas = data_section.get("datas") or []
        for entry in datas:
            table = entry.get("data") or {}
            rows = table.get("data") or []
            columns = table.get("columns") or []
            column_unit = None
            attrs = table.get("attrs") or {}
            if columns and len(columns) > 1:
                value_column = columns[1]
                meta = attrs.get(value_column) or {}
                column_unit = meta.get("unit")
            if column_unit and not unit:
                unit = column_unit
            for row in rows:
                if len(row) < 2:
                    continue
                obs_time = _parse_datetime(row[0])
                if not obs_time:
                    continue
                try:
                    value = float(row[1])
                except (TypeError, ValueError):
                    continue
                extra = {
                    "source": entry.get("source"),
                    "description": entry.get("description"),
                    "unit": column_unit,
                }
                series.append((obs_time, value, extra))
    return series, unit


def _coerce_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _coerce_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        return payload if isinstance(payload, list) else []
    return []


def _normalize_skill_response(response: Any) -> Dict[str, Any]:
    payload = _coerce_json_object(response)
    if payload:
        return payload
    return {"ok": False, "error": f"unexpected iFinD response type: {type(response).__name__}"}


def _payload_data_section(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _coerce_json_object(payload.get("data"))


def _iter_data_entries(data_section: Dict[str, Any]) -> List[Dict[str, Any]]:
    datas = data_section.get("datas") or []
    if isinstance(datas, str):
        datas = _coerce_json_list(datas)
    if not isinstance(datas, list):
        return []
    return [entry for entry in datas if isinstance(entry, dict)]


def _entry_table(entry: Dict[str, Any]) -> Dict[str, Any]:
    return _coerce_json_object(entry.get("data"))


def _extract_payloads(response: Any) -> List[Dict[str, Any]]:
    response = _coerce_json_object(response)
    payloads: List[Dict[str, Any]] = []
    result = response.get("result", {})
    result = result if isinstance(result, dict) else _coerce_json_object(result)
    for block in result.get("content", []):
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = block
        if not text:
            continue
        try:
            payload = json.loads(text) if isinstance(text, str) else text
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _extract_markdown_tables(markdown: str) -> List[pd.DataFrame]:
    tables: List[pd.DataFrame] = []
    current: List[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            current.append(line)
            continue
        if current:
            table = _markdown_table_to_frame(current)
            if table is not None and not table.empty:
                tables.append(table)
            current = []
    if current:
        table = _markdown_table_to_frame(current)
        if table is not None and not table.empty:
            tables.append(table)
    return tables


def _markdown_table_to_frame(lines: List[str]) -> pd.DataFrame | None:
    if len(lines) < 2:
        return None
    headers = [item.strip() for item in lines[0].strip("|").split("|")]
    rows: List[List[str]] = []
    for line in lines[2:]:
        values = [item.strip() for item in line.strip("|").split("|")]
        if len(values) != len(headers):
            continue
        rows.append(values)
    if not rows:
        return None
    return pd.DataFrame(rows, columns=headers)


def _parse_numeric(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    multiplier = 1.0
    if text.endswith("万亿"):
        multiplier = 1e12
        text = text[:-2]
    elif text.endswith("万"):
        multiplier = 1e4
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 1e8
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _parse_trade_date(value: Any) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return _parse_datetime(text)


def fetch_stock_history_dataframe(
    server: MCPServer,
    tool: str,
    query: str,
    value_keyword: str = "收盘价",
) -> pd.DataFrame:
    client = MCPClient(name=server.name, url=server.url, auth_token=server.auth_token)
    response = client.call_tool(tool, {"query": query})
    payloads = _extract_payloads(response)
    frames: List[pd.DataFrame] = []
    for payload in payloads:
        answer = ((payload.get("data") or {}).get("answer")) or ""
        for table in _extract_markdown_tables(answer):
            if "证券代码" not in table.columns or "日期" not in table.columns:
                continue
            value_columns = [col for col in table.columns if value_keyword in col]
            if not value_columns:
                continue
            value_column = value_columns[0]
            renamed = table.rename(
                columns={
                    "证券代码": "code",
                    "证券简称": "name",
                    "日期": "date",
                    value_column: "value",
                }
            )
            subset_cols = [col for col in ["code", "name", "date", "value", "所属板块"] if col in renamed.columns]
            renamed = renamed[subset_cols].copy()
            renamed["date"] = renamed["date"].map(_parse_trade_date)
            renamed["value"] = renamed["value"].map(_parse_numeric)
            renamed = renamed.dropna(subset=["code", "date", "value"])
            if not renamed.empty:
                frames.append(renamed)
    if not frames:
        return pd.DataFrame(columns=["code", "name", "date", "value", "所属板块"])
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    return combined.sort_values(["code", "date"]).reset_index(drop=True)


def search_stock_table(
    server: MCPServer,
    tool: str,
    query: str,
) -> pd.DataFrame:
    client = MCPClient(name=server.name, url=server.url, auth_token=server.auth_token)
    response = client.call_tool(tool, {"query": query})
    payloads = _extract_payloads(response)
    for payload in payloads:
        answer = ((payload.get("data") or {}).get("answer")) or ""
        tables = _extract_markdown_tables(answer)
        if tables:
            return tables[0]
    return pd.DataFrame()


def fetch_series_dataframe(
    server: MCPServer,
    tool: str,
    series_cfg: Dict[str, Any],
    indicator_args: Dict[str, Any] | None,
    start: str,
    end: str,
    code: str,
) -> Tuple[pd.DataFrame, str | None]:
    client = MCPClient(name=server.name, url=server.url, auth_token=server.auth_token)
    args_template: Dict[str, Any] = {}
    if indicator_args:
        args_template.update(indicator_args)
    if series_cfg.get("arguments"):
        args_template.update(series_cfg["arguments"])
    if series_cfg.get("query"):
        args_template["query"] = series_cfg["query"]
    formatted_args = format_arguments(args_template, start, end)
    response = client.call_tool(series_cfg.get("tool") or tool, formatted_args)
    rows, unit = parse_line_series(response)
    if not rows:
        return pd.DataFrame(columns=["date", code]), unit
    df = pd.DataFrame({"date": [row[0] for row in rows], code: [row[1] for row in rows]})
    return df, unit


def _extract_tables_from_payload(payload: Dict[str, Any]) -> List[pd.DataFrame]:
    tables: List[pd.DataFrame] = []
    data_section = _payload_data_section(payload)
    answer = data_section.get("answer") or ""
    tables.extend(_extract_markdown_tables(answer))
    for entry in _iter_data_entries(data_section):
        data_markdown = entry.get("data_markdown") or ""
        tables.extend(_extract_markdown_tables(data_markdown))
    return tables


def _parse_unit_from_header(header: str) -> str | None:
    match = re.search(r"[（(]单位[:：]?\s*([^)）]+)[)）]", header)
    if match:
        return match.group(1).strip()
    return None


def fetch_edb_markdown_dataframe(
    server: MCPServer,
    tool: str,
    query: str,
    code: str,
    value_column: str,
    date_column: str = "日期",
) -> Tuple[pd.DataFrame, str | None]:
    client = MCPClient(name=server.name, url=server.url, auth_token=server.auth_token)
    response = client.call_tool(tool, {"query": query})
    payloads = _extract_payloads(response)
    frames: List[pd.DataFrame] = []
    unit: str | None = None
    for payload in payloads:
        for table in _extract_tables_from_payload(payload):
            if date_column not in table.columns or value_column not in table.columns:
                continue
            if unit is None:
                unit = _parse_unit_from_header(value_column)
            renamed = table.rename(columns={date_column: "date", value_column: code})[["date", code]].copy()
            renamed["date"] = renamed["date"].map(_parse_trade_date)
            renamed[code] = renamed[code].map(_parse_numeric)
            renamed = renamed.dropna(subset=["date", code])
            if not renamed.empty:
                frames.append(renamed)
    if not frames:
        return pd.DataFrame(columns=["date", code]), unit
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"], keep="last")
    combined = combined.sort_values("date").reset_index(drop=True)
    return combined, unit


def _fetch_edb_direct_dataframe(
    query: str,
    code: str,
    value_column: str,
) -> Tuple[pd.DataFrame, str | None]:
    """Fetch an EDB series through the installed iFinD skill adapter.

    The skill adapter returns the MCP envelope under ``data.result`` while the
    project MCP client returns ``result`` directly, so this path normalizes the
    envelope before reusing the same row validation rules.
    """
    module = _load_ifind_skill_call_module()
    response = _normalize_skill_response(module.call("edb", "get_edb_data", {"query": query}))
    if not response.get("ok"):
        raise RuntimeError(f"iFinD EDB request failed: {response}")

    data_section = _coerce_json_object(response.get("data")) if not isinstance(response.get("data"), dict) else response.get("data")
    mcp_result = data_section.get("result") if isinstance(data_section, dict) else None
    mcp_result = _coerce_json_object(mcp_result) or mcp_result or {}
    payloads = _extract_payloads({"result": mcp_result})
    answers=[_payload_data_section(p).get('answer') for p in payloads]
    refusal=next((str(a) for a in answers if a and any(word in str(a) for word in ('超限','权限','额度','限流'))),None)
    if refusal:
        raise RuntimeError(f'iFinD EDB unavailable: {refusal}')
    frames: List[pd.DataFrame] = []
    unit: str | None = None
    for payload in payloads:
        data_section = _payload_data_section(payload)
        for entry in _iter_data_entries(data_section):
            table = _entry_table(entry)
            rows = table.get("data") or []
            columns = table.get("columns") or []
            if not rows or len(columns) < 2:
                continue
            date_index = columns.index("日期") if "日期" in columns else 0
            value_index = columns.index(value_column) if value_column in columns else 1
            if value_index >= len(columns):
                continue
            selected_rows = []
            for row in rows:
                if len(row) <= max(date_index, value_index):
                    continue
                obs_time = _parse_trade_date(row[date_index])
                if not obs_time:
                    continue
                value = _parse_numeric(row[value_index])
                if value is None:
                    continue
                selected_rows.append((obs_time, value))
            if selected_rows:
                actual_column = columns[value_index]
                attrs = table.get("attrs") or {}
                if unit is None:
                    unit = (attrs.get(actual_column) or {}).get("unit")
                frames.append(pd.DataFrame(selected_rows, columns=["date", code]))

    if not frames:
        return pd.DataFrame(columns=["date", code]), unit
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"], keep="last")
    return combined.sort_values("date").reset_index(drop=True), unit


def fetch_edb_direct_dataframe(query, code, value_column, comein_index_name=None):
    """Try the direct EDB gateway, then a separately configured Comein gateway."""
    import re
    dates=re.findall(r'\d{4}-\d{2}-\d{2}',query)
    end=pd.Timestamp(dates[-1]) if dates else None
    primary=pd.DataFrame()
    primary_unit=None
    errors=[]
    try:
        primary,primary_unit=_fetch_edb_direct_dataframe(query,code,value_column)
        if not primary.empty and (end is None or primary['date'].max()>=end):
            return primary,primary_unit
        errors.append('Direct EDB returned no data or did not reach the requested date')
    except Exception as exc:
        errors.append(str(exc))
    try:
        from datahub.adapters.comein_mcp import ComeinMcpClient
        index_name = comein_index_name or re.sub(r'\s*\d{4}-\d{2}-\d{2}\s*(至|到|-)\s*\d{4}-\d{2}-\d{2}\s*', '', query).strip()
        start_month = dates[0].replace('-', '')[:6] if dates else None
        end_month = dates[-1].replace('-', '')[:6] if dates else None
        with ComeinMcpClient(timeout=45) as client:
            response=client.call_tool('get_economic_indicator_data',{
                'index_name': index_name,
                **({'start': start_month} if start_month else {}),
                **({'end': end_month} if end_month else {}),
            })
        frames=[]
        unit=None
        for payload in _extract_payloads({'result':response}):
            section=_payload_data_section(payload)
            metadata=section.get('indicator_description') or []
            if isinstance(metadata,str):
                metadata=_coerce_json_list(metadata)
            frequencies=[str(item.get('freq') or item.get('频率') or '')
                         for item in metadata if isinstance(item,dict)]
            if frequencies and any(frequency not in ('D','日') for frequency in frequencies):
                raise ValueError('Fallback EDB frequency is not daily')
            for table in _extract_tables_from_payload(payload):
                columns=list(table.columns)
                if '日期' not in columns:
                    continue
                value_columns=[column for column in columns if column!='日期']
                if len(value_columns)!=1:
                    continue
                actual_column=value_columns[0]
                if not actual_column.startswith(value_column):
                    raise ValueError('Fallback EDB returned a different indicator')
                actual_unit=_parse_unit_from_header(actual_column)
                if unit and actual_unit and unit!=actual_unit:
                    raise ValueError('Fallback EDB mixed units')
                unit=unit or actual_unit
                frame=table[['日期',actual_column]].rename(columns={'日期':'date',actual_column:code})
                frame['date']=frame['date'].map(_parse_trade_date)
                frame[code]=frame[code].map(_parse_numeric)
                frames.append(frame.dropna())
            for entry in _iter_data_entries(section):
                table=_entry_table(entry)
                columns=table.get('columns') or []
                if '日期' not in columns or value_column not in columns: continue
                metadata=(table.get('attrs') or {}).get(value_column,{})
                if metadata.get('freq')!='D': raise ValueError('Fallback EDB frequency is not daily')
                actual_unit=metadata.get('unit')
                if unit and unit!=actual_unit: raise ValueError('Fallback EDB mixed units')
                unit=actual_unit
                frame=pd.DataFrame(table.get('data') or [],columns=columns)[['日期',value_column]].rename(columns={'日期':'date',value_column:code})
                frame['date']=pd.to_datetime(frame['date'],errors='coerce')
                frame[code]=pd.to_numeric(frame[code],errors='coerce')
                frames.append(frame.dropna())
        if not frames: raise ValueError('Fallback EDB did not return the exact requested indicator')
        result=pd.concat(frames,ignore_index=True).drop_duplicates('date').sort_values('date')
        if end is not None: result=result[result.date<=end]
        if result.empty: raise ValueError('Fallback EDB returned no usable dated values')
        if not primary.empty and primary.date.max()>result.date.max(): return primary,primary_unit
        result.attrs.update(source_id='comein_ifind_edb',raw_payload=response,source_attempts=errors)
        return result,unit
    except Exception as exc:
        if not primary.empty: return primary,primary_unit
        raise RuntimeError(f'All EDB gateways failed: {errors}; Comein: {exc}') from exc
