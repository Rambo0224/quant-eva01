from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from macro_replay.charts import render_chart
from macro_replay.config import load_indicators, load_mcp_servers
from macro_replay.db import ensure_db, record_chart
from macro_replay.pipeline import process_indicator
from macro_replay.source_labels import display_source_label


def load_chart_frame(conn, indicator) -> pd.DataFrame | None:
    chart_cfg = indicator.chart or {}
    y_field = chart_cfg.get("y_field", indicator.id)

    preferred_series = [indicator.id]
    preferred_series.extend(f"{indicator.id}:{series['code']}" for series in (indicator.series or []))

    best_rows = None
    best_score = None
    for series_id in dict.fromkeys(preferred_series):
        rows = conn.execute(
            """
            SELECT obs_time, value
            FROM observations
            WHERE indicator_id = ? AND series_id = ?
            ORDER BY obs_time
            """,
            [indicator.id, series_id],
        ).fetchall()
        if not rows:
            continue
        score = (len(rows), rows[0][0], rows[-1][0])
        if best_score is None or score > best_score:
            best_rows = rows
            best_score = score
    if best_rows:
        return pd.DataFrame(best_rows, columns=["date", y_field])

    rows = conn.execute(
        """
        SELECT obs_time, value
        FROM observations
        WHERE indicator_id = ?
        ORDER BY obs_time
        """,
        [indicator.id],
    ).fetchall()
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["date", y_field])


def main() -> None:
    try:
        servers = load_mcp_servers()
    except FileNotFoundError:
        servers = {}

    conn = ensure_db()
    indicators = load_indicators()

    try:
        for indicator in indicators:
            chart_type = (indicator.chart or {}).get("type")
            if chart_type == "term_structure" or indicator.source in {"manual-excel-term", "manual-excel-line", "manual-excel-multi-line"}:
                process_indicator(indicator, servers)
                print(f"[INFO] Re-rendered {indicator.id} via pipeline")
                continue

            df = load_chart_frame(conn, indicator)
            if df is None or df.empty:
                print(f"[WARN] Skipped {indicator.id}: no observations")
                continue

            html_path, image_path = render_chart(
                df,
                indicator,
                Path("charts") / indicator.id,
                write_image=True,
            )
            record_chart(conn, indicator.id, html_path, image_path, display_source_label(indicator.source))
            print(f"[INFO] Re-rendered {indicator.id}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
