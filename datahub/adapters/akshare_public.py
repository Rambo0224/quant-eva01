from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import akshare as ak
import pandas as pd
import requests


class AkshareSourceUnavailable(RuntimeError):
    """Raised when a public AkShare endpoint is unavailable or changes shape."""


@dataclass(frozen=True)
class AkshareFetchResult:
    frame: pd.DataFrame
    source_id: str
    endpoint: str
    fetched_at: datetime
    note: str = ""


def _require_columns(frame: pd.DataFrame, columns: set[str], endpoint: str) -> None:
    missing = columns.difference(frame.columns)
    if missing:
        raise AkshareSourceUnavailable(f"{endpoint} missing columns: {sorted(missing)}")


def fetch_index_daily(
    symbol: str,
    factor: str,
    start_date: str = "2018-01-01",
    end_date: str | None = None,
) -> AkshareFetchResult:
    endpoint = f"stock_zh_index_daily:{symbol}"
    try:
        raw = ak.stock_zh_index_daily(symbol=symbol)
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(f"{endpoint} failed: {exc}") from exc
    _require_columns(raw, {"date", "close"}, endpoint)
    frame = raw[["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame[factor] = pd.to_numeric(frame.pop("close"), errors="coerce")
    frame = frame.dropna(subset=["date", factor])
    frame = frame[frame["date"] >= pd.Timestamp(start_date)]
    if end_date:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return AkshareFetchResult(
        frame=frame,
        source_id="akshare_public",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="指数日线公共回退；生产环境优先切换到交易所或 iFinD",
    )


def fetch_sse_margin_history(
    start_date: str = "2018-01-01",
    end_date: str | None = None,
) -> AkshareFetchResult:
    endpoint = "stock_margin_sse"
    try:
        raw = ak.stock_margin_sse()
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(f"{endpoint} failed: {exc}") from exc
    _require_columns(raw, {"信用交易日期", "融资余额", "融资买入额", "融券余量金额"}, endpoint)
    frame = raw.rename(
        columns={
            "信用交易日期": "date",
            "融资余额": "margin_balance_sse",
            "融资买入额": "margin_purchase_sse",
            "融券余量金额": "short_balance_sse",
        }
    )[["date", "margin_balance_sse", "margin_purchase_sse", "short_balance_sse"]].copy()
    frame["date"] = pd.to_datetime(frame["date"].astype("string"), format="%Y%m%d", errors="coerce")
    for column in frame.columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
    frame = frame[frame["date"] >= pd.Timestamp(start_date)]
    if end_date:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    return AkshareFetchResult(
        frame=frame.reset_index(drop=True),
        source_id="akshare_public",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="仅上交所口径，不等同于全市场融资融券合计",
    )


def fetch_index_valuation(
    symbol: str,
    start_date: str = "2018-01-01",
    end_date: str | None = None,
) -> AkshareFetchResult:
    endpoint = f"stock_zh_index_value_csindex:{symbol}"
    try:
        raw = ak.stock_zh_index_value_csindex(symbol=symbol)
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(f"{endpoint} failed: {exc}") from exc
    _require_columns(raw, {"日期", "市盈率1", "市盈率2"}, endpoint)
    frame = raw.rename(columns={"日期": "date", "市盈率1": "pe_1", "市盈率2": "pe_2"})[["date", "pe_1", "pe_2"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["pe_1"] = pd.to_numeric(frame["pe_1"], errors="coerce")
    frame["pe_2"] = pd.to_numeric(frame["pe_2"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
    frame = frame[frame["date"] >= pd.Timestamp(start_date)]
    if end_date:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    return AkshareFetchResult(
        frame=frame.reset_index(drop=True),
        source_id="akshare_public",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="保留市盈率1/市盈率2两列，待确认具体估值口径后映射到 PE 指标",
    )


def fetch_50etf_qvix_history(
    start_date: str = "2015-02-09",
    end_date: str | None = None,
) -> AkshareFetchResult:
    endpoint = "index_option_50etf_qvix"
    try:
        raw = ak.index_option_50etf_qvix()
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(f"{endpoint} failed: {exc}") from exc
    _require_columns(raw, {"date", "close"}, endpoint)
    frame = raw[["date", "close"]].rename(columns={"close": "qvix"}).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["qvix"] = pd.to_numeric(frame["qvix"], errors="coerce")
    frame = frame.dropna(subset=["date", "qvix"])
    frame = frame[frame["date"] >= pd.Timestamp(start_date)]
    if end_date:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    return AkshareFetchResult(
        frame=frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True),
        source_id="akshare_public",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="50ETF QVIX 日线；AkShare 文档来源为 optbbs 波动率指数接口",
    )


def fetch_50etf_ivix_history(
    start_date: str = "2015-02-09",
    end_date: str | None = None,
) -> AkshareFetchResult:
    """Fetch the 50ETF option implied-volatility series used for ivix_50."""
    result = fetch_50etf_qvix_history(start_date=start_date, end_date=end_date)
    frame = result.frame.rename(columns={"qvix": "ivix_50"})
    return AkshareFetchResult(
        frame=frame,
        source_id=result.source_id,
        endpoint=result.endpoint,
        fetched_at=result.fetched_at,
        note="AkShare index_option_50etf_qvix: 50ETF 期权波动率指数 QVIX, stored as ivix_50 for the 50ETF implied-volatility chart",
    )


def fetch_etf_history(
    symbol: str,
    start_date: str = "2010-01-01",
    end_date: str | None = None,
    adjust: str = "",
) -> AkshareFetchResult:
    endpoint = "fund_etf_hist_em"
    try:
        raw = ak.fund_etf_hist_em(
            symbol=str(symbol).zfill(6),
            period="daily",
            start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
            end_date=pd.Timestamp(end_date or pd.Timestamp.today().date()).strftime("%Y%m%d"),
            adjust=adjust,
        )
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(f"{endpoint} failed for {symbol}: {exc}") from exc
    if raw is None or raw.empty:
        raise AkshareSourceUnavailable(f"{endpoint} returned no rows for {symbol}")
    required = {"日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "换手率"}
    _require_columns(raw, required, endpoint)
    rename = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "换手率": "turnover_rate",
    }
    frame = raw[[column for column in rename if column in raw.columns]].rename(columns=rename).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
    frame["symbol"] = str(symbol).zfill(6)
    frame["price_adjustment"] = adjust or "none"
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return AkshareFetchResult(
        frame=frame[["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover_rate", "price_adjustment"]],
        source_id="akshare_etf_hist_em",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="东方财富 ETF 历史行情日线；用于主流ETF和行业ETF行情落库",
    )


def fetch_sse_etf_scale(date: str) -> AkshareFetchResult:
    """Fetch Shanghai Stock Exchange ETF share counts for one statistics date."""
    endpoint = "fund_etf_scale_sse"
    compact_date = pd.Timestamp(date).strftime("%Y%m%d")
    data_str = "-".join([compact_date[:4], compact_date[4:6], compact_date[6:]])
    try:
        response = requests.get(
            "https://query.sse.com.cn/commonQuery.do",
            params={
                "isPagination": "true",
                "pageHelp.pageSize": "10000",
                "pageHelp.pageNo": "1",
                "pageHelp.beginPage": "1",
                "pageHelp.cacheSize": "1",
                "pageHelp.endPage": "1",
                "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
                "STAT_DATE": data_str,
            },
            headers={
                "Referer": "https://www.sse.com.cn/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
                ),
            },
            timeout=20,
        )
        response.raise_for_status()
        raw = pd.DataFrame(response.json().get("result", []))
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(f"{endpoint} failed for {date}: {exc}") from exc
    if raw is None or raw.empty:
        return AkshareFetchResult(
            frame=pd.DataFrame(columns=["symbol", "date", "name", "exchange", "shares", "etf_type"]),
            source_id="akshare_etf_scale_sse",
            endpoint=endpoint,
            fetched_at=datetime.now(timezone.utc),
            note="SSE ETF share-count table returned no rows for the requested date",
        )
    raw = raw.rename(
        columns={
            "SEC_CODE": "基金代码",
            "SEC_NAME": "基金简称",
            "ETF_TYPE": "ETF类型",
            "STAT_DATE": "统计日期",
            "TOT_VOL": "基金份额",
        }
    )
    _require_columns(raw, {"基金代码", "基金简称", "统计日期", "基金份额"}, endpoint)
    frame = raw.rename(
        columns={
            "基金代码": "symbol",
            "基金简称": "name",
            "统计日期": "date",
            "基金份额": "shares",
            "ETF类型": "etf_type",
        }
    )[["symbol", "date", "name", "shares", "etf_type"]].copy()
    frame["symbol"] = frame["symbol"].astype("string").str.zfill(6)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["shares"] = pd.to_numeric(frame["shares"], errors="coerce")
    frame["exchange"] = "SSE"
    frame = frame.dropna(subset=["symbol", "date", "shares"]).sort_values(["symbol", "date"])
    return AkshareFetchResult(
        frame=frame[["symbol", "date", "name", "exchange", "shares", "etf_type"]].reset_index(drop=True),
        source_id="akshare_etf_scale_sse",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="上海证券交易所 ETF 基金份额历史查询",
    )


def fetch_szse_etf_scale_history(
    start_date: str,
    end_date: str,
) -> AkshareFetchResult:
    """Fetch Shenzhen Stock Exchange ETF share counts for a bounded date range."""
    endpoint = "fund_scale_daily_szse"
    start_text = pd.Timestamp(start_date).strftime("%Y%m%d")
    end_text = pd.Timestamp(end_date).strftime("%Y%m%d")
    try:
        raw = ak.fund_scale_daily_szse(start_date=start_text, end_date=end_text, symbol="ETF")
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(
            f"{endpoint} failed for {start_date} to {end_date}: {exc}"
        ) from exc
    if raw is None or raw.empty:
        return AkshareFetchResult(
            frame=pd.DataFrame(columns=["symbol", "date", "name", "exchange", "shares"]),
            source_id="akshare_etf_scale_szse_daily",
            endpoint=endpoint,
            fetched_at=datetime.now(timezone.utc),
            note="SZSE ETF daily scale table returned no rows for the requested range",
        )
    _require_columns(raw, {"日期", "基金代码", "基金简称", "基金份额"}, endpoint)
    frame = raw.rename(
        columns={
            "日期": "date",
            "基金代码": "symbol",
            "基金简称": "name",
            "基金份额": "shares",
        }
    )[["symbol", "date", "name", "shares"]].copy()
    frame["symbol"] = frame["symbol"].astype("string").str.zfill(6)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["shares"] = pd.to_numeric(frame["shares"], errors="coerce")
    frame["exchange"] = "SZSE"
    frame = frame.dropna(subset=["symbol", "date", "shares"]).sort_values(["symbol", "date"])
    return AkshareFetchResult(
        frame=frame[["symbol", "date", "name", "exchange", "shares"]].reset_index(drop=True),
        source_id="akshare_etf_scale_szse_daily",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        note="深圳证券交易所 ETF 基金规模日频数据",
    )


def fetch_50etf_option_pcr(
    date: str,
) -> pd.DataFrame:
    endpoint = "option_daily_stats_sse"
    try:
        raw = ak.option_daily_stats_sse(date=pd.Timestamp(date).strftime("%Y%m%d"))
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(f"{endpoint} failed for {date}: {exc}") from exc
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "pcr_volume", "pcr_oi"])

    def column_containing(*parts: str) -> str:
        for column in raw.columns:
            text = str(column)
            if all(part in text for part in parts):
                return column
        raise AkshareSourceUnavailable(f"{endpoint} missing columns containing {parts}")

    code_column = column_containing("合约标的", "代码")
    row = raw[raw[code_column].astype("string") == "510050"].copy()
    if row.empty:
        return pd.DataFrame(columns=["date", "pcr_volume", "pcr_oi"])
    row = row.iloc[0]
    put_volume = pd.to_numeric(row[column_containing("认沽成交量")], errors="coerce")
    call_volume = pd.to_numeric(row[column_containing("认购成交量")], errors="coerce")
    # AkShare has used both "合约数" and "合约总数" in this endpoint.
    put_oi = pd.to_numeric(row[column_containing("未平仓", "认沽", "合约")], errors="coerce")
    call_oi = pd.to_numeric(row[column_containing("未平仓", "认购", "合约")], errors="coerce")
    if pd.isna(call_volume) or call_volume <= 0 or pd.isna(call_oi) or call_oi <= 0:
        return pd.DataFrame(columns=["date", "pcr_volume", "pcr_oi"])
    return pd.DataFrame(
        [{
            "date": pd.Timestamp(date),
            "pcr_volume": float(put_volume / call_volume),
            "pcr_oi": float(put_oi / call_oi),
        }]
    )


def fetch_trade_dates(start_date: str, end_date: str) -> pd.DatetimeIndex:
    """Return official mainland China trading dates for a requested range."""
    endpoint = "tool_trade_date_hist_sina"
    try:
        raw = ak.tool_trade_date_hist_sina()
    except Exception as exc:  # pragma: no cover - depends on live public endpoint
        raise AkshareSourceUnavailable(f"{endpoint} failed: {exc}") from exc
    _require_columns(raw, {"trade_date"}, endpoint)
    dates = pd.to_datetime(raw["trade_date"], errors="coerce").dropna()
    return pd.DatetimeIndex(
        dates[(dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))]
    ).normalize().drop_duplicates().sort_values()


def normalize_limit_pool_counts(
    limit_up: pd.DataFrame,
    limit_down: pd.DataFrame,
    broken_board: pd.DataFrame,
    date: str,
) -> pd.DataFrame:
    broken_count = len(broken_board)
    limit_up_count = len(limit_up)
    limit_down_count = len(limit_down)
    touched_limit_count = limit_up_count + broken_count
    explosive_ratio = broken_count / touched_limit_count if touched_limit_count else None
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(date),
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
                "broken_board_count": broken_count,
                "explosive_ratio": explosive_ratio,
            }
        ]
    )


def fetch_limit_pool_counts(date: str) -> AkshareFetchResult:
    endpoints = {
        "limit_up": "stock_zt_pool_em",
        "limit_down": "stock_zt_pool_dtgc_em",
        "broken_board": "stock_zt_pool_zbgc_em",
    }
    frames: dict[str, pd.DataFrame] = {}
    for key, endpoint in endpoints.items():
        function = getattr(ak, endpoint, None)
        if function is None:
            raise AkshareSourceUnavailable(f"{endpoint} is not available in the installed AkShare version")
        try:
            frames[key] = function(date=date)
        except Exception as exc:  # pragma: no cover - depends on live public endpoint
            raise AkshareSourceUnavailable(f"{endpoint} failed for {date}: {exc}") from exc
    if all(frame.empty for frame in frames.values()):
        raise AkshareSourceUnavailable(f"No limit-pool rows returned for {date}; likely a non-trading day")
    frame = normalize_limit_pool_counts(frames["limit_up"], frames["limit_down"], frames["broken_board"], date)
    return AkshareFetchResult(
        frame=frame,
        source_id="akshare_public",
        endpoint="stock_zt_pool_em+stock_zt_pool_dtgc_em+stock_zt_pool_zbgc_em",
        fetched_at=datetime.now(timezone.utc),
        note="涨停池、跌停池和炸板池的日度计数；未包含总交易家数，因此不计算涨跌停占比",
    )
