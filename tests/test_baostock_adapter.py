import pandas as pd

from datahub.adapters.baostock_equity import is_a_share_code, normalize_history, normalize_symbol


def test_baostock_code_normalization_and_filtering() -> None:
    assert normalize_symbol("sh.600000") == "600000"
    assert normalize_symbol("000001") == "000001"
    assert is_a_share_code("sh.600000")
    assert is_a_share_code("sz.300750")
    assert is_a_share_code("bj.920001")
    assert not is_a_share_code("sh.000001")


def test_baostock_history_keeps_market_metadata_in_extra() -> None:
    raw = pd.DataFrame(
        [
            {
                "date": "2024-01-02",
                "code": "sh.600000",
                "open": "10",
                "high": "11",
                "low": "9",
                "close": "10.5",
                "preclose": "10",
                "volume": "100",
                "amount": "1000",
                "adjustflag": "3",
                "turn": "0.2",
                "tradestatus": "1",
                "pctChg": "5",
                "isST": "0",
            }
        ]
    )
    result = normalize_history(raw)
    assert result.loc[0, "symbol"] == "600000"
    assert result.loc[0, "turnover_rate"] == 0.2
    assert result.loc[0, "extra"]["isST"] == "0"


def test_baostock_daily_all_normalization_keeps_each_symbol() -> None:
    raw = pd.DataFrame(
        [
            {"date": "2024-01-02", "code": "sh.600000", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "100", "amount": "1000", "turn": "0.2", "tradestatus": "1"},
            {"date": "2024-01-02", "code": "sz.000001", "open": "8", "high": "9", "low": "7", "close": "8.5", "volume": "200", "amount": "1600", "turn": "0.3", "tradestatus": "1"},
        ]
    )

    result = normalize_history(raw)

    assert len(result) == 2
    assert set(result["symbol"]) == {"600000", "000001"}
