import pandas as pd

from scripts.compute_baostock_market_breadth import compute_market_breadth


def test_market_breadth_uses_board_price_limits() -> None:
    bars = pd.DataFrame([
        {"symbol": "600001", "date": "2026-01-02", "high": 11.00, "low": 10.50, "close": 11.00, "prev_close": 10.00, "is_st": "0"},
        {"symbol": "600002", "date": "2026-01-02", "high": 10.40, "low": 9.49, "close": 9.50, "prev_close": 10.00, "is_st": "1"},
        {"symbol": "300001", "date": "2026-01-02", "high": 12.00, "low": 10.50, "close": 11.50, "prev_close": 10.00, "is_st": "0"},
        {"symbol": "688001", "date": "2026-01-02", "high": 12.00, "low": 10.50, "close": 12.00, "prev_close": 10.00, "is_st": "0"},
        {"symbol": "830001", "date": "2026-01-02", "high": 13.00, "low": 10.50, "close": 13.00, "prev_close": 10.00, "is_st": "0"},
    ])
    result = compute_market_breadth(bars)

    row = result.iloc[0]
    assert row["limit_up_count"] == 3
    assert row["limit_down_count"] == 1
    assert row["limit_up_ratio"] == 3 / 5
    assert row["limit_down_ratio"] == 1 / 5


def test_market_breadth_calculates_explosive_ratio_from_touched_limit_up() -> None:
    bars = pd.DataFrame([
        {"symbol": "600001", "date": "2026-01-02", "high": 11.00, "low": 10.20, "close": 10.80, "prev_close": 10.00, "is_st": "0"},
        {"symbol": "600002", "date": "2026-01-02", "high": 11.00, "low": 10.20, "close": 11.00, "prev_close": 10.00, "is_st": "0"},
        {"symbol": "600003", "date": "2026-01-02", "high": 10.80, "low": 10.20, "close": 10.50, "prev_close": 10.00, "is_st": "0"},
    ])
    result = compute_market_breadth(bars)

    row = result.iloc[0]
    assert row["limit_up_count"] == 1
    assert row["explosive_ratio"] == 1 / 2


def test_market_turnover_rate_is_float_value_weighted() -> None:
    bars = pd.DataFrame([
        {
            "symbol": "600001",
            "date": "2026-01-02",
            "high": 11.00,
            "low": 10.50,
            "close": 11.00,
            "prev_close": 10.00,
            "is_st": "0",
            "amount": 100.0,
            "turnover_rate": 2.0,
        },
        {
            "symbol": "600002",
            "date": "2026-01-02",
            "high": 10.40,
            "low": 9.80,
            "close": 10.20,
            "prev_close": 10.00,
            "is_st": "0",
            "amount": 300.0,
            "turnover_rate": 6.0,
        },
    ])
    result = compute_market_breadth(bars)

    # amount sum / sum(amount / individual_turnover_decimal) * 100
    assert result.loc[0, "turnover_rate"] == 4.0


def test_market_breadth_calculates_advance_decline_line() -> None:
    bars = pd.DataFrame([
        {"symbol": "600001", "date": "2026-01-02", "high": 11.00, "low": 10.20, "close": 11.00, "prev_close": 10.00, "is_st": "0"},
        {"symbol": "600002", "date": "2026-01-02", "high": 10.40, "low": 9.80, "close": 9.90, "prev_close": 10.00, "is_st": "0"},
        {"symbol": "600003", "date": "2026-01-02", "high": 10.40, "low": 9.80, "close": 10.30, "prev_close": 10.00, "is_st": "0"},
        {"symbol": "600001", "date": "2026-01-03", "high": 11.10, "low": 10.80, "close": 10.90, "prev_close": 11.00, "is_st": "0"},
        {"symbol": "600002", "date": "2026-01-03", "high": 10.20, "low": 9.70, "close": 10.10, "prev_close": 9.90, "is_st": "0"},
        {"symbol": "600003", "date": "2026-01-03", "high": 10.50, "low": 10.00, "close": 10.40, "prev_close": 10.30, "is_st": "0"},
    ])
    result = compute_market_breadth(bars)

    assert list(result["advance_decline_net"]) == [1, 1]
    assert list(result["advance_decline_line"]) == [1, 2]
