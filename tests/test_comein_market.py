import pandas as pd

from datahub.adapters.comein_market import _markdown_table, _normalize


def test_etf_markdown_table_normalizes_ohlcv():
    text = """| 日期 | 开 | 高 | 低 | 收 | 成交量 |
| --- | --- | --- | --- | --- | --- |
| 2026-09-01 | 0.700 | 0.702 | 0.679 | 0.680 | 370,544,182 |
"""
    table = _markdown_table(text)
    frame = _normalize(
        table,
        dict(zip(table.columns, ["date", "open", "high", "low", "close", "volume"])),
        "raw",
    )
    assert frame.loc[0, "date"] == pd.Timestamp("2026-09-01")
    assert frame.loc[0, "volume"] == 370_544_182
    assert frame.loc[0, "price_adjustment"] == "raw"


def test_normalizer_rejects_incomplete_ohlc_schema():
    table = pd.DataFrame({"date": ["2026-09-01"], "open": [1.0]})
    try:
        _normalize(table, {}, "raw")
    except RuntimeError as exc:
        assert "missing OHLC" in str(exc)
    else:
        raise AssertionError("incomplete response must not be accepted")
