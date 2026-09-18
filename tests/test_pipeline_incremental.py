from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from macro_replay import db, pipeline
from macro_replay.config import Indicator


class IncrementalRefreshTests(unittest.TestCase):
    def test_fetch_window_is_independent_from_display_window(self) -> None:
        indicator = Indicator(
            id="fetch-window-test",
            title="Fetch Window Test",
            description="",
            server="",
            tool="fred",
            arguments={
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
                "fetch_start_date": "2010-01-01",
                "fetch_end_date": "2026-07-18",
            },
            chart={"type": "line", "y_field": "value"},
            theme="test",
            source="fred",
            series=[
                {
                    "code": "value",
                    "source": "fred",
                    "url": "https://example.com/value.csv",
                }
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            db_path = data_dir / "replay.duckdb"
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", db_path):
                refreshed = pd.DataFrame(
                    {
                        "date": pd.to_datetime(["2010-01-01", "2026-07-18"]),
                        "value": [1.0, 2.0],
                    }
                )
                with patch.object(pipeline, "fetch_series_frame", return_value=(refreshed, "index")) as fetch:
                    with patch.object(
                        pipeline,
                        "render_chart",
                        return_value=(Path("charts/fetch-window-test.html"), Path("charts/fetch-window-test.png")),
                    ):
                        pipeline.process_indicator(indicator, {})

        self.assertEqual(fetch.call_args.args[2:], ("2010-01-01", "2026-07-18", {}))

    def test_process_indicator_replaces_recent_window_instead_of_append_only(self) -> None:
        indicator = Indicator(
            id="usd-liquidity-test",
            title="USD Liquidity Test",
            description="",
            server="",
            tool="fred",
            arguments={
                "start_date": "2024-01-01",
                "end_date": "2024-01-04",
                "revision_lookback_days": 2,
            },
            chart={"type": "line", "y_field": "liquidity"},
            theme="usd-liquidity",
            source="fred",
            series=[
                {
                    "code": "liquidity",
                    "source": "fred",
                    "url": "https://example.com/liquidity.csv",
                }
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            db_path = data_dir / "replay.duckdb"
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", db_path):
                conn = db.ensure_db()
                try:
                    db.upsert_observations(
                        conn,
                        indicator.id,
                        f"{indicator.id}:liquidity",
                        "fred",
                        [
                            (pd.Timestamp("2024-01-01").to_pydatetime(), 1.0, {}),
                            (pd.Timestamp("2024-01-02").to_pydatetime(), 2.0, {}),
                            (pd.Timestamp("2024-01-03").to_pydatetime(), 3.0, {}),
                        ],
                        unit="index",
                    )
                finally:
                    conn.close()

                refreshed = pd.DataFrame(
                    {
                        "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
                        "liquidity": [1.0, 20.0, 30.0, 40.0],
                    }
                )

                with patch.object(pipeline, "fetch_series_frame", return_value=(refreshed, "index")):
                    with patch.object(
                        pipeline,
                        "render_chart",
                        return_value=(Path("charts/usd-liquidity-test.html"), Path("charts/usd-liquidity-test.png")),
                    ):
                        pipeline.process_indicator(indicator, {})

                verify_conn = db.ensure_db()
                try:
                    rows = verify_conn.execute(
                        """
                        SELECT obs_time, value
                        FROM observations
                        WHERE indicator_id = ? AND series_id = ?
                        ORDER BY obs_time
                        """,
                        [indicator.id, f"{indicator.id}:liquidity"],
                    ).fetchall()
                finally:
                    verify_conn.close()

        self.assertEqual(
            rows,
            [
                (pd.Timestamp("2024-01-01"), 1.0),
                (pd.Timestamp("2024-01-02"), 20.0),
                (pd.Timestamp("2024-01-03"), 30.0),
                (pd.Timestamp("2024-01-04"), 40.0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
