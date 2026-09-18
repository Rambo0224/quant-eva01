import pandas as pd

from datahub.adapters.akshare_public import normalize_limit_pool_counts


def test_normalize_limit_pool_counts() -> None:
    result = normalize_limit_pool_counts(
        pd.DataFrame({"代码": ["1", "2", "3"]}),
        pd.DataFrame({"代码": ["4"]}),
        pd.DataFrame({"代码": ["5", "6"]}),
        "2026-07-16",
    )
    row = result.iloc[0]
    assert row["limit_up_count"] == 3
    assert row["limit_down_count"] == 1
    assert row["broken_board_count"] == 2
    assert row["explosive_ratio"] == 2 / 5
