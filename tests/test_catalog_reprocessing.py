from datahub.transforms.ashare import annualized_volatility


def test_index_volatility_formula_is_available() -> None:
    import pandas as pd

    close = pd.Series(range(1, 30), dtype="float64")
    result = annualized_volatility(close, window=20)
    assert result.notna().sum() == 9
