from __future__ import annotations

import unittest

import pandas as pd

from macro_replay.charts import build_chart_figure
from macro_replay.config import Indicator


def _indicator(chart: dict) -> Indicator:
    return Indicator(
        id="chart-test",
        title="Chart Test",
        description="",
        server="",
        tool="test",
        arguments={},
        chart=chart,
        theme="test",
        source="test",
    )


class ChartSparseDataTests(unittest.TestCase):
    def test_single_point_line_chart_uses_lines_only(self) -> None:
        frame = pd.DataFrame({"date": ["2026-07-17"], "value": [1.25]})

        figure = build_chart_figure(frame, _indicator({"type": "line", "y_field": "value"}))

        self.assertEqual(len(figure.data), 1)
        self.assertEqual(figure.data[0].mode, "lines")

    def test_multi_axis_single_points_use_lines_only(self) -> None:
        frame = pd.DataFrame(
            {"date": ["2026-07-17"], "left": [1.0], "right": [2.0]}
        )
        chart = {
            "type": "multi_axis_line",
            "y_field": "left",
            "left_fields": ["left"],
            "right_fields": ["right"],
        }

        figure = build_chart_figure(frame, _indicator(chart))

        self.assertEqual([trace.mode for trace in figure.data], ["lines", "lines"])

    def test_stacked_single_points_use_lines_only(self) -> None:
        frame = pd.DataFrame({"date": ["2026-07-17"], "cash": [1.0], "debt": [2.0]})
        chart = {
            "type": "stacked_area",
            "y_field": "cash",
            "stack_fields": ["cash", "debt"],
        }

        figure = build_chart_figure(frame, _indicator(chart))

        self.assertEqual([trace.mode for trace in figure.data], ["lines", "lines"])


if __name__ == "__main__":
    unittest.main()
