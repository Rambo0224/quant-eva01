import pytest

from datahub.adapters.openbb_yfinance import normalize_openbb_symbol
from datahub.adapters.tushare_pro import TushareSourceUnavailable, fetch_equity_history


def test_openbb_symbol_mapping_for_a_shares():
    assert normalize_openbb_symbol("600000") == "600000.SS"
    assert normalize_openbb_symbol("000001") == "000001.SZ"
    assert normalize_openbb_symbol("SH600000") == "600000.SS"
    assert normalize_openbb_symbol("000300.SS") == "000300.SS"


def test_tushare_requires_token_without_injecting_a_client():
    with pytest.raises(TushareSourceUnavailable, match="TUSHARE_TOKEN"):
        fetch_equity_history("600000.SH", "2025-01-01", "2025-01-10", token="")
