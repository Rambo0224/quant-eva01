from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from datahub.adapters.manual_excel import load_manual_line_series
from macro_replay.config import Indicator


class ManualExcelAdapterTests(unittest.TestCase):
    def test_load_manual_line_series_reads_rows_from_excel(self) -> None:
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
            },
            chart={"type": "line", "y_field": "value"},
            theme="sample",
            source="manual-excel-line",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            workbook_path = Path(tmpdir) / "sample.xlsx"
            frame = pd.DataFrame(
                {
                    0: ["date", "2024-01-01", "2024-01-02", None, None],
                    1: ["value", 10, 20, None, None],
                }
            )
            with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
                frame.to_excel(writer, sheet_name="Sheet1", index=False, header=False)

            result = load_manual_line_series(indicator, Path(tmpdir))

        self.assertEqual(result.file_path, "sample.xlsx")
        self.assertEqual(result.sheet_name, "Sheet1")
        self.assertEqual(result.rows_read, 2)
        self.assertEqual(list(result.frame.columns), ["date", "value"])
        self.assertEqual(result.frame["value"].tolist(), [10.0, 20.0])


if __name__ == "__main__":
    unittest.main()
