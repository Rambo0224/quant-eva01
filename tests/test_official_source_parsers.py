import pandas as pd

from datahub.adapters.exchange_official import normalize_sse_margin_table


def test_normalize_sse_margin_table() -> None:
    raw = pd.DataFrame(
        {
            "信用交易日期": ["20260716", "20260715"],
            "融资余额(元)": ["1,430,821,453,142", "1,444,782,928,188"],
            "融资买入额(元)": ["106,047,371,889", "121,624,335,799"],
            "融券余量金额(元)": ["13,223,339,987", "13,759,732,441"],
        }
    )

    result = normalize_sse_margin_table(raw)

    assert list(result["date"].dt.strftime("%Y-%m-%d")) == ["2026-07-15", "2026-07-16"]
    assert result.loc[result["date"] == pd.Timestamp("2026-07-16"), "margin_balance"].iat[0] == 1430821453142
