import pandas as pd

from scripts.fetch_etf_share_history import _trade_dates


def test_trade_dates_extends_stale_calendar_with_business_days(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pd.DataFrame(
        [
            {"calendar_date": "2026-07-16", "is_trading_day": 1},
            {"calendar_date": "2026-07-17", "is_trading_day": 1},
            {"calendar_date": "2026-07-18", "is_trading_day": 0},
        ]
    ).to_csv(data_dir / "baostock_trade_dates.csv", index=False)

    result = _trade_dates("2026-07-16", "2026-07-21")

    assert [item.strftime("%Y-%m-%d") for item in result] == [
        "2026-07-16",
        "2026-07-17",
        "2026-07-20",
        "2026-07-21",
    ]
