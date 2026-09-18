from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services import status_service
from macro_replay import db


class StatusServiceTests(unittest.TestCase):
    def test_load_latest_manual_imports_returns_latest_row_per_indicator(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            db_path = data_dir / "replay.duckdb"
            with patch.object(db, "DATA_DIR", data_dir), patch.object(db, "DB_PATH", db_path):
                conn = db.ensure_db()
                try:
                    db.record_manual_import(
                        conn,
                        indicator_id="copper-inventory",
                        source_id="manual-excel-multi-line",
                        file_path="manual.xlsx",
                        sheet_name="铜库存",
                        file_mtime=None,
                        import_mode="replace_since",
                        rows_read=10,
                        rows_written=9,
                        date_min=None,
                        date_max=None,
                        status="success",
                    )
                    db.record_manual_import(
                        conn,
                        indicator_id="copper-inventory",
                        source_id="manual-excel-multi-line",
                        file_path="manual.xlsx",
                        sheet_name="铜库存",
                        file_mtime=None,
                        import_mode="replace_since",
                        rows_read=12,
                        rows_written=11,
                        date_min=None,
                        date_max=None,
                        status="success",
                    )
                finally:
                    conn.close()

                with patch.object(status_service, "DB_PATH", db_path):
                    latest = status_service.load_latest_manual_imports(["copper-inventory"])

        self.assertEqual(latest["copper-inventory"]["rows_read"], 12)
        self.assertEqual(latest["copper-inventory"]["rows_written"], 11)


if __name__ == "__main__":
    unittest.main()
