"""Bindings between configured market sources and their acquisition adapters."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd


MarketFetcher = Callable[[str, str, str], pd.DataFrame]


def _comein_stock(symbol: str, start: str, end: str) -> pd.DataFrame:
    from datahub.adapters.comein_market import fetch_stock_history
    return fetch_stock_history(symbol, start, end)


def _akshare_stock(symbol: str, start: str, end: str) -> pd.DataFrame:
    from datahub.adapters.akshare_equity import fetch_equity_history
    return fetch_equity_history(symbol, start, end, "qfq").frame


def _baostock_stock(symbol: str, start: str, end: str) -> pd.DataFrame:
    from datahub.adapters.baostock_equity import BaoStockClient
    exchange = "sh" if symbol.startswith(("5", "6")) else "bj" if symbol.startswith(("4", "8", "9")) else "sz"
    with BaoStockClient(adjustflag="2") as client:
        return client.fetch_history(f"{exchange}.{symbol}", start, end).frame


def _comein_etf(symbol: str, start: str, end: str) -> pd.DataFrame:
    from datahub.adapters.comein_market import fetch_etf_history
    return fetch_etf_history(symbol, start, end)


def _akshare_etf(symbol: str, start: str, end: str) -> pd.DataFrame:
    from datahub.adapters.akshare_public import fetch_etf_history
    return fetch_etf_history(symbol, start, end, "").frame


def _baostock_etf(symbol: str, start: str, end: str) -> pd.DataFrame:
    from datahub.adapters.baostock_equity import BaoStockClient
    exchange = "sz" if symbol.startswith("159") else "sh"
    with BaoStockClient(adjustflag="3") as client:
        return client.fetch_history(f"{exchange}.{symbol}", start, end).frame


def _akshare_index(symbol: str, start: str, end: str) -> pd.DataFrame:
    from datahub.adapters.akshare_public import fetch_index_daily
    return fetch_index_daily(symbol, "close", start, end).frame


def _eastmoney_index(symbol: str, start: str, end: str) -> pd.DataFrame:
    import akshare as ak
    raw = ak.index_zh_a_hist(symbol=symbol[-6:], period="daily", start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    frame = raw.rename(columns={"日期": "date", "收盘": "close"})[["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _comein_index(symbol: str, start: str, end: str) -> pd.DataFrame:
    from datahub.adapters.comein_market import fetch_index_history
    return fetch_index_history(symbol, start, end)


ADAPTERS: dict[str, MarketFetcher] = {
    "comein_stock_daily": _comein_stock,
    "akshare_stock_qfq": _akshare_stock,
    "baostock_stock_qfq": _baostock_stock,
    "comein_etf_daily": _comein_etf,
    "akshare_etf_daily": _akshare_etf,
    "baostock_etf_daily": _baostock_etf,
    "akshare_index_daily": _akshare_index,
    "eastmoney_index_daily": _eastmoney_index,
    "comein_index_daily": _comein_index,
}


def fetch(adapter: str, symbol: str, start: str, end: str) -> pd.DataFrame:
    try:
        return ADAPTERS[adapter](symbol, start, end)
    except KeyError as exc:
        raise ValueError(f"No market adapter registered for {adapter}") from exc


def validate_market_sources(configuration: dict) -> None:
    """Reject a candidate list that is not fully bound to source modules."""
    for kind, candidates in configuration["market"]["candidates"].items():
        if not candidates:
            raise ValueError(f"{kind}: candidate source list is empty")
        for candidate in candidates:
            if set(candidate) != {"source"}:
                raise ValueError(f"{kind}: candidates may only declare source")
            source_id = candidate["source"]
            source = configuration["sources"].get(source_id)
            adapter = source and source.get("adapter")
            if adapter not in ADAPTERS:
                raise ValueError(f"{kind}: {source_id} is not bound to a registered source adapter")
