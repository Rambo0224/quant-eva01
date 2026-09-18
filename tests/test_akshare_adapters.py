import pandas as pd

from datahub.adapters.akshare_public import (
    fetch_50etf_ivix_history,
    fetch_50etf_option_pcr,
    fetch_50etf_qvix_history,
    fetch_etf_history,
    fetch_sse_etf_scale,
    fetch_szse_etf_scale_history,
    fetch_index_valuation,
    fetch_trade_dates,
)


def test_index_valuation_requires_both_pe_columns(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def stock_zh_index_value_csindex(symbol):
            return pd.DataFrame(
                {
                    "日期": ["2026-07-16"],
                    "市盈率1": [14.67],
                    "市盈率2": [17.41],
                }
            )

    monkeypatch.setattr("datahub.adapters.akshare_public.ak", FakeAk)
    result = fetch_index_valuation("000300")
    assert list(result.frame.columns) == ["date", "pe_1", "pe_2"]
    assert result.frame.loc[0, "pe_2"] == 17.41


def test_qvix_history_normalizes_daily_close(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def index_option_50etf_qvix():
            return pd.DataFrame(
                {
                    "date": ["2015-02-09"],
                    "open": [28.8],
                    "high": [29.0],
                    "low": [27.8],
                    "close": [28.63],
                }
            )

    monkeypatch.setattr("datahub.adapters.akshare_public.ak", FakeAk)
    result = fetch_50etf_qvix_history()
    assert list(result.frame.columns) == ["date", "qvix"]
    assert result.frame.loc[0, "qvix"] == 28.63


def test_ivix_50_history_reuses_50etf_qvix_endpoint(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def index_option_50etf_qvix():
            return pd.DataFrame({"date": ["2015-02-09"], "close": [28.63]})

    monkeypatch.setattr("datahub.adapters.akshare_public.ak", FakeAk)
    result = fetch_50etf_ivix_history()
    assert result.endpoint == "index_option_50etf_qvix"
    assert list(result.frame.columns) == ["date", "ivix_50"]
    assert result.frame.loc[0, "ivix_50"] == 28.63


def test_etf_history_normalizes_ohlcv(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def fund_etf_hist_em(symbol, period, start_date, end_date, adjust):
            return pd.DataFrame(
                {
                    "日期": ["2026-07-17"],
                    "开盘": [2.985],
                    "收盘": [2.931],
                    "最高": [2.996],
                    "最低": [2.916],
                    "成交量": [14525750],
                    "成交额": [4290446418.0],
                    "换手率": [17.63],
                }
            )

    monkeypatch.setattr("datahub.adapters.akshare_public.ak", FakeAk)
    result = fetch_etf_history("510050", "2026-07-17", "2026-07-17")
    assert result.source_id == "akshare_etf_hist_em"
    assert list(result.frame.columns) == [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "turnover_rate",
        "price_adjustment",
    ]
    assert result.frame.loc[0, "symbol"] == "510050"
    assert result.frame.loc[0, "close"] == 2.931


def test_sse_etf_scale_normalizes_share_counts(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "result": [
                    {
                        "SEC_CODE": "510050",
                        "SEC_NAME": "50ETF",
                        "ETF_TYPE": "单市",
                        "STAT_DATE": "2026-07-20",
                        "TOT_VOL": "8322067000",
                    }
                ]
            }

    monkeypatch.setattr("datahub.adapters.akshare_public.requests.get", lambda *args, **kwargs: FakeResponse())
    result = fetch_sse_etf_scale("2026-07-20")
    assert result.source_id == "akshare_etf_scale_sse"
    assert list(result.frame.columns) == ["symbol", "date", "name", "exchange", "shares", "etf_type"]
    assert result.frame.loc[0, "symbol"] == "510050"
    assert result.frame.loc[0, "shares"] == 8322067000.0


def test_szse_etf_scale_history_normalizes_share_counts(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def fund_scale_daily_szse(start_date, end_date, symbol):
            assert start_date == "20260701"
            assert end_date == "20260720"
            assert symbol == "ETF"
            return pd.DataFrame(
                {
                    "日期": ["2026-07-20"],
                    "基金代码": ["159915"],
                    "基金简称": ["创业板ETF易方达"],
                    "基金份额": ["17639450000"],
                }
            )

    monkeypatch.setattr("datahub.adapters.akshare_public.ak", FakeAk)
    result = fetch_szse_etf_scale_history("2026-07-01", "2026-07-20")
    assert result.source_id == "akshare_etf_scale_szse_daily"
    assert list(result.frame.columns) == ["symbol", "date", "name", "exchange", "shares"]
    assert result.frame.loc[0, "symbol"] == "159915"
    assert result.frame.loc[0, "exchange"] == "SZSE"
    assert result.frame.loc[0, "shares"] == 17639450000.0


def test_50etf_option_pcr_uses_sse_volume_and_open_interest(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def option_daily_stats_sse(date):
            return pd.DataFrame(
                {
                    "合约标的代码": ["510050"],
                    "认购成交量": [100],
                    "认沽成交量": [80],
                    "未平仓认购合约总数": [200],
                    "未平仓认沽合约总数": [120],
                }
            )

    monkeypatch.setattr("datahub.adapters.akshare_public.ak", FakeAk)
    result = fetch_50etf_option_pcr("2026-07-16")
    assert result.loc[0, "pcr_volume"] == 0.8
    assert result.loc[0, "pcr_oi"] == 0.6


def test_trade_dates_filters_to_requested_range(monkeypatch) -> None:
    class FakeAk:
        @staticmethod
        def tool_trade_date_hist_sina():
            return pd.DataFrame({"trade_date": ["2026-07-15", "2026-07-16", "2026-07-17"]})

    monkeypatch.setattr("datahub.adapters.akshare_public.ak", FakeAk)
    result = fetch_trade_dates("2026-07-16", "2026-07-17")
    assert list(result.strftime("%Y-%m-%d")) == ["2026-07-16", "2026-07-17"]
