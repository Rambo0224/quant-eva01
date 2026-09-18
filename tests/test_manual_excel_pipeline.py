from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from macro_replay import db, pipeline
from macro_replay.config import Indicator


class ManualExcelPipelineTests(unittest.TestCase):
    def test_manual_line_stores_full_file_before_display_filter(self) -> None:
        indicator = Indicator(
            id="manual-line-window-test",
            title="Manual Line Window Test",
            description="",
            server="",
            tool="",
            arguments={
                "workbook_path": "sample.xlsx",
                "sheet_name": "Sheet1",
                "date_col": "A",
                "value_col": "B",
                "data_start_row": 2,
                "start_date": "2024-01-02",
                "end_date": "2024-01-02",
            },
            chart={"type": "line", "y_field": "value", "unit": "index"},
            theme="sample",
            source="manual-excel-line",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workbook_path = root / "sample.xlsx"
            frame = pd.DataFrame({0: ["date", "2024-01-01", "2024-01-02"], 1: ["value", 10, 20]})
            with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
                frame.to_excel(writer, sheet_name="Sheet1", index=False, header=False)

            data_dir = root / "data"
            db_path = data_dir / "replay.duckdb"
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", db_path):
                with patch.object(pipeline, "PROJECT_ROOT", root):
                    with patch.object(
                        pipeline,
                        "render_chart",
                        return_value=(Path("charts/manual-line-window-test.html"), Path("charts/manual-line-window-test.png")),
                    ):
                        conn = db.ensure_db()
                        try:
                            pipeline.process_manual_line_indicator(indicator, conn)
                            rows = conn.execute(
                                "SELECT obs_time, value FROM observations WHERE indicator_id = ? ORDER BY obs_time",
                                [indicator.id],
                            ).fetchall()
                        finally:
                            conn.close()

        self.assertEqual(rows, [(pd.Timestamp("2024-01-01"), 10.0), (pd.Timestamp("2024-01-02"), 20.0)])

    def test_process_manual_line_indicator_writes_observations_and_import_log(self) -> None:
        indicator = Indicator(
            id="manual-line-test",
            title="Manual Line Test",
            description="",
            server="",
            tool="",
            arguments={
                "workbook_path": "sample.xlsx",
                "sheet_name": "Sheet1",
                "date_col": "A",
                "value_col": "B",
                "data_start_row": 2,
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
            },
            chart={"type": "line", "y_field": "value", "unit": "index"},
            theme="sample",
            source="manual-excel-line",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workbook_path = root / "sample.xlsx"
            frame = pd.DataFrame(
                {
                    0: ["date", "2024-01-01", "2024-01-02"],
                    1: ["value", 10, 20],
                }
            )
            with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
                frame.to_excel(writer, sheet_name="Sheet1", index=False, header=False)

            data_dir = root / "data"
            db_path = data_dir / "replay.duckdb"
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", db_path):
                with patch.object(pipeline, "PROJECT_ROOT", root):
                    with patch.object(
                        pipeline,
                        "render_chart",
                        return_value=(Path("charts/manual-line-test.html"), Path("charts/manual-line-test.png")),
                    ):
                        conn = db.ensure_db()
                        try:
                            pipeline.process_manual_line_indicator(indicator, conn)
                            obs_rows = conn.execute(
                                """
                                SELECT obs_time, value
                                FROM observations
                                WHERE indicator_id = ?
                                ORDER BY obs_time
                                """,
                                [indicator.id],
                            ).fetchall()
                            import_row = conn.execute(
                                """
                                SELECT file_path, sheet_name, rows_read, rows_written, status
                                FROM manual_import_logs
                                WHERE indicator_id = ?
                                ORDER BY imported_at DESC
                                LIMIT 1
                                """,
                                [indicator.id],
                            ).fetchone()
                        finally:
                            conn.close()

        self.assertEqual(obs_rows, [(pd.Timestamp("2024-01-01"), 10.0), (pd.Timestamp("2024-01-02"), 20.0)])
        self.assertEqual(import_row, ("sample.xlsx", "Sheet1", 2, 2, "success"))


if __name__ == "__main__":
    unittest.main()
