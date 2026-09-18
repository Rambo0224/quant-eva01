from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb

from macro_replay.config import Indicator
from macro_replay import dashboard_service


class DashboardServiceFallbackTests(unittest.TestCase):
    def test_load_observation_rows_returns_empty_frame_when_duckdb_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "locked.duckdb"
            db_path.touch()
            with patch.object(dashboard_service, "DB_PATH", db_path):
                with patch.object(dashboard_service.duckdb, "connect", side_effect=duckdb.Error("locked")):
                    frame = dashboard_service.load_observation_rows("sample-indicator")

        self.assertTrue(frame.empty)
        self.assertEqual(
            list(frame.columns),
            ["date", "series_id", "source_id", "value", "unit", "extra"],
        )

    def test_load_dashboard_cards_degrades_gracefully_when_chart_query_fails(self) -> None:
        indicator = Indicator(
            id="sample-indicator",
            title="Sample Indicator",
            description="",
            server="",
            tool="fred",
            arguments={},
            chart={"type": "line", "y_field": "value"},
            theme="sample-theme",
            source="fred",
            series=[{"code": "value", "source": "fred", "url": "https://example.com"}],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "locked.duckdb"
            db_path.touch()
            with patch.object(dashboard_service, "DB_PATH", db_path):
                with patch.object(dashboard_service, "load_indicators", return_value=[indicator]):
                    with patch.object(dashboard_service.duckdb, "connect", side_effect=duckdb.Error("locked")):
                        cards = dashboard_service.load_dashboard_cards()

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], "sample-indicator")
        self.assertIsNone(cards[0]["latest_value"])
        self.assertIsNone(cards[0]["image_path"])


if __name__ == "__main__":
    unittest.main()
