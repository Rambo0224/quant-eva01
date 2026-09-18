import json

import pandas as pd

from datahub.adapters.ifind_market import extract_download_urls, normalize_date_column, parse_statistical_summary
from macro_replay.sources import ifind as ifind_source


def test_extract_download_urls_deduplicates_csv_links():
    answer = "下载URL为： http://example.test/a.csv；另一个 http://example.test/a.csv"
    assert extract_download_urls(answer) == ("http://example.test/a.csv",)


def test_normalize_date_column_handles_trade_date():
    frame = normalize_date_column(pd.DataFrame({"日期": [20260716], "收盘价": [10.0]}))
    assert frame.loc[0, "date"] == pd.Timestamp("2026-07-16")


def test_fetch_edb_direct_dataframe_reads_structured_rows(monkeypatch):
    payload = {
        "code": 1,
        "data": {
            "answer": "",
            "datas": [
                {
                    "data": {
                        "data": [["2026-07-17", 2657733985720.61], ["2025-07-17", 1541127261059.18]],
                        "columns": ["日期", "沪深两市:股票:成交金额"],
                        "attrs": {"沪深两市:股票:成交金额": {"unit": "元"}},
                    }
                }
            ],
        },
    }

    class FakeCallModule:
        @staticmethod
        def call(server_type, tool_name, arguments):
            assert server_type == "edb"
            assert tool_name == "get_edb_data"
            assert "query" in arguments
            return {
                "ok": True,
                "data": {
                    "result": {
                        "content": [{"text": json.dumps(payload, ensure_ascii=False)}]
                    }
                },
            }

    monkeypatch.setattr(ifind_source, "_load_ifind_skill_call_module", lambda: FakeCallModule)
    frame, unit = ifind_source.fetch_edb_direct_dataframe(
        "A股成交额 2025-07-17至2026-07-17",
        "turnover_total",
        "沪深两市:股票:成交金额",
    )

    assert len(frame) == 2
    assert frame["date"].min() == pd.Timestamp("2025-07-17")
    assert frame["date"].max() == pd.Timestamp("2026-07-17")
    assert unit == "元"


def test_fetch_edb_direct_dataframe_accepts_json_string_response(monkeypatch):
    payload = {
        "code": 1,
        "data": {
            "datas": [
                {
                    "data": {
                        "data": [["2026-07-17", "12.5"]],
                        "columns": ["日期", "指标值"],
                        "attrs": {"指标值": {"unit": "亿元"}},
                    }
                }
            ],
        },
    }

    class FakeCallModule:
        @staticmethod
        def call(server_type, tool_name, arguments):
            return json.dumps(
                {
                    "ok": True,
                    "data": {
                        "result": {
                            "content": [{"text": json.dumps(payload, ensure_ascii=False)}]
                        }
                    },
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(ifind_source, "_load_ifind_skill_call_module", lambda: FakeCallModule)
    frame, unit = ifind_source.fetch_edb_direct_dataframe("query", "value", "指标值")

    assert len(frame) == 1
    assert frame.loc[0, "date"] == pd.Timestamp("2026-07-17")
    assert frame.loc[0, "value"] == 12.5
    assert unit == "亿元"


def test_parse_statistical_summary_reads_median():
    answer = """| 日期 | 指标名称 | 均值 | 最大值 | 中位数 | 最小值 |\n| --- | --- | --- | --- | --- | --- |\n| 20260716 | 市盈率(PE,TTM) | 60.4 | 34433.56 | 24.98 | -13001.21 |"""
    frame = parse_statistical_summary(answer)
    assert frame.loc[0, "metric"] == "市盈率(PE,TTM)"
    assert frame.loc[0, "median"] == 24.98


def test_fetch_edb_direct_dataframe_accepts_nested_json_string_data(monkeypatch):
    payload = {
        "code": 1,
        "data": json.dumps(
            {
                "datas": [
                    {
                        "data": json.dumps(
                            {
                                "data": [["2026-07-17", "12.5"]],
                                "columns": ["date", "value_col"],
                                "attrs": {"value_col": {"unit": "unit"}},
                            }
                        )
                    }
                ]
            }
        ),
    }

    class FakeCallModule:
        @staticmethod
        def call(server_type, tool_name, arguments):
            return {
                "ok": True,
                "data": json.dumps(
                    {
                        "result": {
                            "content": [{"text": json.dumps(payload, ensure_ascii=False)}]
                        }
                    },
                    ensure_ascii=False,
                ),
            }

    monkeypatch.setattr(ifind_source, "_load_ifind_skill_call_module", lambda: FakeCallModule)
    frame, unit = ifind_source.fetch_edb_direct_dataframe("query", "value", "value_col")

    assert len(frame) == 1
    assert frame.loc[0, "date"] == pd.Timestamp("2026-07-17")
    assert frame.loc[0, "value"] == 12.5
    assert unit == "unit"
