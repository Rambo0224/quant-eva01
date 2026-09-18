from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from typing import Iterable

import pandas as pd
import requests


SSE_MARGIN_URL = "https://www.sse.com.cn/market/othersdata/margin/sum/"
SZSE_MARGIN_URL = "https://www.szse.cn/disclosure/margin/margin/index.html"


class SourceUnavailable(RuntimeError):
    """Raised when an official source cannot be parsed or reached safely."""


@dataclass(frozen=True)
class SourceFetchResult:
    frame: pd.DataFrame
    source_id: str
    url: str
    fetched_at: datetime
    note: str = ""


def _parse_numeric(values: Iterable[object]) -> pd.Series:
    return pd.to_numeric(
        pd.Series(values, dtype="string")
        .str.replace(",", "", regex=False)
        .str.replace("--", "", regex=False)
        .str.strip(),
        errors="coerce",
    )


def normalize_sse_margin_table(table: pd.DataFrame) -> pd.DataFrame:
    required = {"信用交易日期", "融资余额(元)", "融资买入额(元)", "融券余量金额(元)"}
    missing = required.difference(table.columns)
    if missing:
        raise SourceUnavailable(f"SSE margin table missing columns: {sorted(missing)}")

    renamed = table.rename(
        columns={
            "信用交易日期": "date",
            "融资余额(元)": "margin_balance",
            "融资买入额(元)": "margin_purchase",
            "融券余量金额(元)": "short_balance",
            "融资融券余额(元)": "margin_total_balance",
            "融券余量": "short_quantity",
            "融券卖出量": "short_sale_volume",
        }
    ).copy()
    renamed["date"] = pd.to_datetime(renamed["date"].astype("string"), format="%Y%m%d", errors="coerce")
    for column in (
        "margin_balance",
        "margin_purchase",
        "short_balance",
        "margin_total_balance",
        "short_quantity",
        "short_sale_volume",
    ):
        if column in renamed.columns:
            renamed[column] = _parse_numeric(renamed[column])

    columns = [
        "date",
        "margin_balance",
        "margin_purchase",
        "short_balance",
        "margin_total_balance",
        "short_quantity",
        "short_sale_volume",
    ]
    return (
        renamed[[column for column in columns if column in renamed.columns]]
        .dropna(subset=["date"])
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )


def _read_margin_table(html: str) -> pd.DataFrame:
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError as exc:
        raise SourceUnavailable(f"No HTML tables found: {exc}") from exc

    for table in tables:
        if {"信用交易日期", "融资余额(元)"}.issubset(table.columns):
            return normalize_sse_margin_table(table)
    raise SourceUnavailable("No recognized margin summary table found")


def fetch_sse_margin_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    timeout: float = 30.0,
) -> SourceFetchResult:
    response = requests.get(
        SSE_MARGIN_URL,
        headers={"User-Agent": "OmniSignal/0.1 (+local research terminal)"},
        timeout=timeout,
    )
    response.raise_for_status()
    frame = _read_margin_table(response.text)
    if start_date:
        frame = frame[frame["date"] >= pd.Timestamp(start_date)]
    if end_date:
        frame = frame[frame["date"] <= pd.Timestamp(end_date)]
    return SourceFetchResult(
        frame=frame.reset_index(drop=True),
        source_id="sse_official",
        url=SSE_MARGIN_URL,
        fetched_at=datetime.now(timezone.utc),
        note="上交所融资融券汇总数据；字段口径以页面注释为准",
    )


def fetch_szse_margin_summary(*args, **kwargs) -> SourceFetchResult:
    raise SourceUnavailable(
        "深交所融资融券页面通过 iframe 加载数据；需在下一步锁定其实际数据接口后再接入，避免静默抓取错误页面。"
    )
