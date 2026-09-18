from datahub.adapters.comein_mcp import ComeinTool
from scripts.fetch_comein_futures import parse_futures_markdown


def test_parse_comein_futures_markdown():
    frame = parse_futures_markdown(
        "| 时间 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |\n|---|---:|---:|---:|---:|---:|\n| 2026-07-17 00:00:00 | 1 | 2 | 0.5 | 1.5 | 10 |"
    )
    assert len(frame) == 1
    assert frame.loc[0, "close"] == 1.5
    assert frame.loc[0, "volume"] == 10

