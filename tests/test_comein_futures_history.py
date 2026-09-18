from __future__ import annotations

import pandas as pd

from scripts.fetch_comein_futures_history import monthly_contract_codes, select_dominant_contract


def test_monthly_contract_codes_cover_requested_months() -> None:
    assert monthly_contract_codes("IF", "2025-07-17", "2025-09-01") == ["IF2507", "IF2508", "IF2509"]


def test_select_dominant_contract_chooses_highest_daily_volume() -> None:
    frame = pd.DataFrame(
        [
            {"date": "2025-07-17", "close": 4000, "volume": 100, "symbol": "IF2507"},
            {"date": "2025-07-17", "close": 4010, "volume": 200, "symbol": "IF2508"},
            {"date": "2025-07-18", "close": 4020, "volume": 150, "symbol": "IF2508"},
        ]
    )

    result = select_dominant_contract(frame, "2025-07-17", "2025-07-18")

    assert result["symbol"].tolist() == ["IF2508", "IF2508"]
    assert result["close"].tolist() == [4010, 4020]
