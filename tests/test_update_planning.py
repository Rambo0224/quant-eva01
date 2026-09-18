import pandas as pd

from datahub.sync.planning import _contiguous_windows


def test_missing_standard_dates_are_planned_as_exact_trading_windows():
    expected = [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02"),
                pd.Timestamp("2026-09-03"), pd.Timestamp("2026-09-04")]
    missing = {expected[0].date(), expected[2].date(), expected[3].date()}
    assert _contiguous_windows(missing, tuple(expected)) == (
        ("2026-09-01", "2026-09-01"),
        ("2026-09-03", "2026-09-04"),
    )
