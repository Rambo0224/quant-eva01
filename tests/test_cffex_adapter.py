import pandas as pd

from datahub.adapters.cffex import (
    days_to_expiry,
    index_futures_expiry_date,
    main_contract_frame,
    normalize_cffex_daily,
    select_main_contracts,
)


def _sample() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": "IF2607", "date": "2026-07-16", "close": "4100", "volume": 100, "open_interest": 200, "variety": "IF"},
            {"symbol": "IF2608", "date": "2026-07-16", "close": "4110", "volume": 300, "open_interest": 100, "variety": "IF"},
            {"symbol": "IC2607", "date": "2026-07-16", "close": "6000", "volume": 20, "open_interest": 500, "variety": "IC"},
            {"symbol": "IH2607", "date": "2026-07-16", "close": "2800", "volume": 40, "open_interest": 600, "variety": "IH"},
            {"symbol": "IO2607-C-4000", "date": "2026-07-16", "close": "10", "volume": 999, "open_interest": 999, "variety": "IO"},
        ]
    )


def test_normalize_filters_options_and_coerces_values():
    frame = normalize_cffex_daily(_sample())
    assert set(frame["variety"]) == {"IF", "IC", "IH"}
    assert frame["close"].dtype.kind in "fi"


def test_select_main_contract_prefers_volume_then_open_interest():
    selected = select_main_contracts(_sample())
    assert selected.set_index("variety").loc["IF", "symbol"] == "IF2608"


def test_main_contract_frame_is_wide():
    wide = main_contract_frame(_sample())
    assert list(wide.columns) == ["date", "if_close", "ic_close", "ih_close"]
    assert wide.loc[0, "if_close"] == 4110


def test_index_futures_expiry_uses_third_friday_and_trading_calendar():
    trade_dates = pd.to_datetime(["2026-07-16", "2026-07-17", "2026-07-20"])
    assert index_futures_expiry_date("IF2607", trade_dates) == pd.Timestamp("2026-07-17")
    assert days_to_expiry(pd.Timestamp("2026-07-16"), "IF2607", trade_dates) == 1


def test_index_futures_expiry_moves_to_previous_trading_day_for_holiday():
    trade_dates = pd.to_datetime(["2026-07-15", "2026-07-16", "2026-07-20"])
    assert index_futures_expiry_date("IF2607", trade_dates) == pd.Timestamp("2026-07-16")
