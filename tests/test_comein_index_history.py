from __future__ import annotations

import json
import unittest

from scripts.fetch_comein_index_history import _normalize_series, parse_comein_json


class ComeinIndexHistoryTests(unittest.TestCase):
    def test_parse_comein_json_extracts_data_array(self) -> None:
        result = {"content": [{"type": "text", "text": json.dumps({"data": [{"close": 1}]})}]}

        self.assertEqual(parse_comein_json(result), [{"close": 1}])

    def test_normalize_series_filters_date_range_and_deduplicates(self) -> None:
        rows = [
            {"trading_day": "2025-07-17", "close": "10"},
            {"trading_day": "2025-07-18", "close": "11"},
            {"trading_day": "2025-07-18", "close": "12"},
            {"trading_day": "2026-07-19", "close": "99"},
        ]

        frame = _normalize_series(rows, "close", "2025-07-17", "2026-07-18")

        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.iloc[-1]["close"], 12)


if __name__ == "__main__":
    unittest.main()
