from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import socket
import time

import pandas as pd


SOURCE_ID = "baostock_history_k_data"
ENDPOINT = "baostock.query_history_k_data_plus"
HISTORY_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,"
    "tradestatus,pctChg,isST"
)


class BaoStockUnavailable(RuntimeError):
    """Raised when BaoStock cannot return a usable result."""


class _CheckedSocket:
    """BaoStock's receive loop does not handle EOF; convert it to an error."""
    def __init__(self, wrapped):
        self.wrapped=wrapped

    def __getattr__(self, name):
        return getattr(self.wrapped,name)

    def recv(self, *args, **kwargs):
        result=self.wrapped.recv(*args,**kwargs)
        if not result:
            raise ConnectionError('BaoStock closed the connection before completing the response')
        return result


@dataclass(frozen=True)
class BaoStockFetchResult:
    frame: pd.DataFrame
    source_id: str
    endpoint: str
    fetched_at: datetime
    note: str = ""


def normalize_symbol(code: str) -> str:
    value = str(code).strip().lower()
    return value.split(".", 1)[-1].zfill(6)


def is_a_share_code(code: str) -> bool:
    value = str(code).strip().lower()
    if "." not in value:
        return False
    exchange, number = value.split(".", 1)
    if not number.isdigit() or len(number) != 6:
        return False
    if exchange == "sh":
        return number.startswith(("600", "601", "603", "605", "688", "689"))
    if exchange == "sz":
        return number.startswith(("000", "001", "002", "003", "300", "301"))
    if exchange == "bj":
        return number.startswith(("4", "8", "92"))
    return False


class BaoStockClient:
    def __init__(self, adjustflag: str = "3", connect_timeout: float = 3.0, read_timeout: float = 15.0):
        self.adjustflag = str(adjustflag)
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self._bs = None
        self._previous_default_timeout = None

    def _preflight_connection(self) -> None:
        try:
            from baostock.common import contants as cons

            with socket.create_connection(
                (cons.BAOSTOCK_SERVER_IP, cons.BAOSTOCK_SERVER_PORT),
                timeout=self.connect_timeout,
            ):
                return
        except Exception as exc:
            raise BaoStockUnavailable(
                f"BaoStock endpoint unavailable before login: public-api.baostock.com:10030 ({exc})"
            ) from exc

    def __enter__(self) -> "BaoStockClient":
        try:
            import baostock as bs
        except Exception as exc:  # pragma: no cover - optional dependency
            raise BaoStockUnavailable("baostock is not installed") from exc
        self._preflight_connection()
        self._previous_default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(self.read_timeout)
        self._bs = bs
        last_error = None
        try:
            for attempt in range(3):
                login = bs.login()
                if login.error_code == "0":
                    from baostock.common import context
                    context.default_socket=_CheckedSocket(context.default_socket)
                    return self
                last_error = f"{login.error_code} {login.error_msg}"
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
            raise BaoStockUnavailable(f"BaoStock login failed after retries: {last_error}")
        except Exception:
            socket.setdefaulttimeout(self._previous_default_timeout)
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._bs is not None:
            self._bs.logout()
        socket.setdefaulttimeout(self._previous_default_timeout)

    def fetch_universe(self, day: str) -> pd.DataFrame:
        result = self._bs.query_all_stock(day=day)
        rows = []
        while result.error_code == "0" and result.next():
            rows.append(result.get_row_data())
        if result.error_code != "0":
            raise BaoStockUnavailable(f"query_all_stock failed: {result.error_code} {result.error_msg}")
        frame = pd.DataFrame(rows, columns=result.fields)
        if frame.empty or "code" not in frame.columns:
            raise BaoStockUnavailable("query_all_stock returned no code rows")
        frame = frame[frame["code"].map(is_a_share_code)].copy()
        frame["symbol"] = frame["code"].map(normalize_symbol)
        frame["exchange_code"] = frame["code"]
        basic = self._bs.query_stock_basic()
        basic_rows = []
        while basic.error_code == "0" and basic.next():
            basic_rows.append(basic.get_row_data())
        if basic.error_code != "0":
            raise BaoStockUnavailable(f"query_stock_basic failed: {basic.error_code} {basic.error_msg}")
        basic_frame = pd.DataFrame(basic_rows, columns=basic.fields)
        self.security_metadata = basic_frame.copy()
        basic_frame = basic_frame[["code", "ipoDate"]].rename(columns={"ipoDate": "ipo_date"})
        frame = frame.merge(basic_frame, on="code", how="left")
        frame['universe_date'] = day
        return frame[["symbol", "exchange_code", "code_name", "tradeStatus", "ipo_date", "universe_date"]].drop_duplicates("symbol").sort_values("symbol")

    def fetch_history(self, code: str, start_date: str, end_date: str) -> BaoStockFetchResult:
        result = self._bs.query_history_k_data_plus(
            code,
            HISTORY_FIELDS,
            start_date=pd.Timestamp(start_date).strftime("%Y-%m-%d"),
            end_date=pd.Timestamp(end_date).strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag=self.adjustflag,
        )
        rows = []
        while result.error_code == "0" and result.next():
            rows.append(result.get_row_data())
        if result.error_code != "0":
            raise BaoStockUnavailable(f"query_history_k_data_plus failed for {code}: {result.error_code} {result.error_msg}")
        raw = pd.DataFrame(rows, columns=result.fields)
        if raw.empty:
            raise BaoStockUnavailable(f"query_history_k_data_plus returned no rows for {code}")
        return BaoStockFetchResult(
            frame=normalize_history(raw, adjustflag=self.adjustflag),
            source_id=SOURCE_ID,
            endpoint=ENDPOINT,
            fetched_at=datetime.now(timezone.utc),
            note=f"BaoStock daily history; adjustflag={self.adjustflag}",
        )

    def fetch_trade_dates(self, start_date: str, end_date: str) -> pd.DataFrame:
        result = self._bs.query_trade_dates(
            start_date=pd.Timestamp(start_date).strftime("%Y-%m-%d"),
            end_date=pd.Timestamp(end_date).strftime("%Y-%m-%d"),
        )
        rows = []
        while result.error_code == "0" and result.next():
            rows.append(result.get_row_data())
        if result.error_code != "0":
            raise BaoStockUnavailable(f"query_trade_dates failed: {result.error_code} {result.error_msg}")
        return pd.DataFrame(rows, columns=result.fields)

    def fetch_daily_history_all(self, date: str) -> BaoStockFetchResult:
        result = self._bs.query_daily_history_k_AStock(
            date=pd.Timestamp(date).strftime("%Y-%m-%d")
        )
        rows = []
        while result.error_code == "0" and result.next():
            rows.append(result.get_row_data())
        if result.error_code != "0":
            raise BaoStockUnavailable(
                f"query_daily_history_k_AStock failed for {date}: {result.error_code} {result.error_msg}"
            )
        raw = pd.DataFrame(rows, columns=result.fields)
        if raw.empty:
            raise BaoStockUnavailable(f"query_daily_history_k_AStock returned no rows for {date}")
        return BaoStockFetchResult(
            frame=normalize_history(raw, adjustflag=self.adjustflag),
            source_id=SOURCE_ID,
            endpoint="baostock.query_daily_history_k_AStock",
            fetched_at=datetime.now(timezone.utc),
            note=f"BaoStock all A-share daily history; adjustflag={self.adjustflag}",
        )


def normalize_history(frame: pd.DataFrame, adjustflag: str = "3") -> pd.DataFrame:
    numeric_columns = ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pctChg"]
    normalized = frame.copy()
    normalized["symbol"] = normalized["code"].map(normalize_symbol)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for column in numeric_columns:
        if column in normalized.columns:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if "tradestatus" in normalized.columns:
        normalized = normalized[normalized["tradestatus"].fillna("1").astype(str).eq("1")]
    normalized = normalized.dropna(subset=["date", "open", "high", "low", "close"])
    normalized["price_adjustment"] = str(adjustflag)
    extra_columns = ["isST", "tradestatus", "adjustflag"]
    normalized["extra"] = normalized.apply(
        lambda row: {column: row[column] for column in extra_columns if column in normalized.columns and pd.notna(row[column])},
        axis=1,
    )
    return normalized[
        ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turn", "extra", "price_adjustment"]
    ].rename(columns={"turn": "turnover_rate"}).sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last").reset_index(drop=True)


def cached_universe_path() -> Path:
    return Path("data") / "baostock_universe.csv"
