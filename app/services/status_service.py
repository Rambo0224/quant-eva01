from __future__ import annotations

from typing import Any

import duckdb

from macro_replay.db import DB_PATH


def _connect_read_only():
    if not DB_PATH.exists():
        return None
    try:
        return duckdb.connect(str(DB_PATH), read_only=True)
    except duckdb.Error:
        return None


def load_latest_manual_imports(indicator_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
    conn = _connect_read_only()
    if conn is None:
        return {}

    try:
        rows = conn.execute(
            """
            SELECT
                indicator_id,
                source_id,
                file_path,
                sheet_name,
                file_mtime,
                import_mode,
                rows_read,
                rows_written,
                date_min,
                date_max,
                status,
                note,
                error_message,
                imported_at
            FROM manual_import_logs
            ORDER BY imported_at DESC
            """
        ).fetchall()
    except duckdb.Error:
        conn.close()
        return {}
    finally:
        conn.close()

    indicator_filter = set(indicator_ids or [])
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        indicator_id = row[0]
        if indicator_filter and indicator_id not in indicator_filter:
            continue
        if indicator_id in latest:
            continue
        latest[indicator_id] = {
            "indicator_id": indicator_id,
            "source_id": row[1],
            "file_path": row[2],
            "sheet_name": row[3],
            "file_mtime": row[4],
            "import_mode": row[5],
            "rows_read": row[6],
            "rows_written": row[7],
            "date_min": row[8],
            "date_max": row[9],
            "status": row[10],
            "note": row[11] or "",
            "error_message": row[12] or "",
            "imported_at": row[13],
        }
    return latest

