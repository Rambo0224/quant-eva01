import pandas as pd

from datahub.adapters.eastmoney_market import _normalize_rows


def test_normalize_eastmoney_snapshot_rows_and_numeric_fields():
    frame = _normalize_rows([
        {"f12": "000001", "f14": "A", "f2": "10.2", "f3": "1.5", "f5": "100", "f6": "2000", "f8": "3.2", "f9": "12", "f10": "0.9", "f23": "1.4"},
        {"f12": "bad", "f14": "B", "f2": "1", "f3": "0", "f5": "1", "f6": "1", "f8": "1", "f9": "1", "f10": "1", "f23": "1"},
    ])
    assert frame["code"].tolist() == ["000001"]
    assert frame.loc[0, "amount"] == 2000
    assert frame.loc[0, "pb"] == 1.4
    assert pd.api.types.is_numeric_dtype(frame["turnover_rate"])
