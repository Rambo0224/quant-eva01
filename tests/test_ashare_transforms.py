import pandas as pd

from datahub.transforms.ashare import annualized_volatility, cross_section_statistics, safe_ratio


def test_safe_ratio_masks_zero_denominator() -> None:
    result = safe_ratio(pd.Series([2.0, 1.0]), pd.Series([1.0, 0.0]))
    assert result.iloc[0] == 2.0
    assert pd.isna(result.iloc[1])


def test_annualized_volatility_has_window_warmup() -> None:
    close = pd.Series([100, 101, 100, 102, 103, 101], dtype="float64")
    result = annualized_volatility(close, window=3)
    assert result.iloc[:3].isna().all()
    assert result.iloc[-1] > 0


def test_cross_section_statistics_calculates_breadth() -> None:
    rows = []
    for code, prices in {"A": [11] * 20, "B": [9] * 20}.items():
        for day, close in enumerate(prices, start=1):
            rows.append({"date": f"2026-01-{day:02d}", "code": code, "open": 10, "close": close})
    result = cross_section_statistics(pd.DataFrame(rows))
    assert "advance_ratio_20d" in result.columns
    assert result.loc[result["date"] == pd.Timestamp("2026-01-20"), "advance_ratio_20d"].iat[0] == 0.5
