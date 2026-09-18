"""Validated daily-market adapters backed by the Comein Finance MCP service."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .comein_mcp import ComeinMcpClient, ComeinMcpUnavailable


def _text(result: dict) -> str:
    text = "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")
    if not text:
        raise ComeinMcpUnavailable("Comein response did not contain text content")
    return text


def _json_payload(result: dict) -> dict:
    try:
        payload = json.loads(_text(result))
    except json.JSONDecodeError as exc:
        raise ComeinMcpUnavailable("Comein response was not structured JSON") from exc
    if not isinstance(payload, dict) or payload.get("code", 200) != 200:
        raise ComeinMcpUnavailable(f"Comein request failed: {payload.get('msg', payload)}")
    return payload


def _markdown_table(text: str) -> pd.DataFrame:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        raise ComeinMcpUnavailable("Comein ETF response contained no Markdown table")
    headers = [cell.strip() for cell in lines[0].strip("|").split("|")]
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]
    if not rows or any(len(row) != len(headers) for row in rows):
        raise ComeinMcpUnavailable("Comein ETF Markdown table was malformed")
    return pd.DataFrame(rows, columns=headers)


def _normalize(frame: pd.DataFrame, mapping: dict[str, str], adjustment: str) -> pd.DataFrame:
    result = frame.rename(columns=mapping).copy()
    required = ["date", "open", "high", "low", "close"]
    if not set(required).issubset(result.columns):
        raise ComeinMcpUnavailable(f"Comein market response missing OHLC columns: {list(frame.columns)}")
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        if column in result:
            result[column] = pd.to_numeric(result[column].astype(str).str.replace(",", "", regex=False), errors="coerce")
    result["price_adjustment"] = adjustment
    return result.dropna(subset=required).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_stock_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch daily front-adjusted A-share OHLCV from Comein."""
    fields = ["period_end_date", "front_adjust_open_price", "front_adjust_high_price", "front_adjust_low_price", "front_adjust_close_price", "volume", "amount"]
    with ComeinMcpClient(timeout=60) as client:
        payload = _json_payload(client.call_tool("get_stock_kline", {
            "query": str(symbol).zfill(6), "freq": "1d", "startDateTime": f"{start} 00:00:00",
            "endDateTime": f"{end} 23:59:59", "order": "asc", "limit": 2000, "fields": fields,
        }))
    return _normalize(pd.DataFrame(payload.get("data", {}).get("bars", [])), {
        "period_end_date": "date", "front_adjust_open_price": "open", "front_adjust_high_price": "high",
        "front_adjust_low_price": "low", "front_adjust_close_price": "close",
    }, "qfq")


def fetch_etf_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch unadjusted exchange-traded fund daily OHLCV from Comein."""
    left = pd.Timestamp(start).normalize()
    right = pd.Timestamp(end).normalize()
    frames: list[pd.DataFrame] = []
    with ComeinMcpClient(timeout=60) as client:
        # A 2,000-calendar-day request remains comfortably below the vendor's
        # 3,000-row ceiling, including markets with unusually few holidays.
        while left <= right:
            chunk_end = min(left + pd.Timedelta(days=1999), right)
            response = client.call_tool("get_exchange_fund_quote_history", {
                "query_text": str(symbol).zfill(6), "start_date": left.date().isoformat(),
                "end_date": chunk_end.date().isoformat(), "fields": ["open", "high", "low", "close", "volume"],
                "limit": 3000,
            })
            body = _text(response)
            try:
                table = _markdown_table(body)
            except ComeinMcpUnavailable:
                # Pre-listing chunks have no table. Transport and provider errors
                # remain failures instead of being misreported as an empty period.
                no_data = (not response.get("isError")) and any(token in body.lower() for token in ("无数据", "未查询到", "no data", "not found"))
                if not no_data:
                    raise
                left = chunk_end + pd.Timedelta(days=1)
                continue
            columns = list(table.columns)
            if len(columns) < 6:
                raise ComeinMcpUnavailable(f"Comein ETF response missing OHLCV columns: {columns}")
            frames.append(_normalize(table, dict(zip(columns[:6], ["date", "open", "high", "low", "close", "volume"])), "raw"))
            left = chunk_end + pd.Timedelta(days=1)
    if not frames:
        raise ComeinMcpUnavailable(f"Comein returned no ETF history for {symbol}")
    return pd.concat(frames, ignore_index=True).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _verified_trade_days(frame: pd.DataFrame) -> pd.DataFrame:
    path = Path(__file__).resolve().parents[2] / "data" / "baostock_trade_dates.csv"
    if not path.exists():
        return frame
    calendar = pd.read_csv(path)
    if not {"calendar_date", "is_trading_day"}.issubset(calendar.columns):
        return frame
    days = pd.to_datetime(calendar.loc[calendar["is_trading_day"].astype(str).isin(["1", "1.0"]), "calendar_date"], errors="coerce")
    return frame[frame["date"].dt.normalize().isin(set(days.dropna()))].reset_index(drop=True)


def fetch_index_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch index daily close, discarding dates absent from the official calendar."""
    with ComeinMcpClient(timeout=60) as client:
        response = client.call_tool("searchIndexQuotation", {
            "mode": "kline", "queries": [str(symbol)[-6:]], "start_date": start,
            "end_date": end, "period_type": "D", "limit": 5000,
        })
    data = pd.DataFrame(_json_payload(response).get("data", []))
    if not {"trading_day", "close"}.issubset(data.columns):
        raise ComeinMcpUnavailable(f"Comein index response missing date/close columns: {list(data.columns)}")
    result = data[["trading_day", "close"]].rename(columns={"trading_day": "date"})
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result = result.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
    return _verified_trade_days(result).reset_index(drop=True)
