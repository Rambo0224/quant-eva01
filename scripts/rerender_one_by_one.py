from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from macro_replay.config import load_indicators
from macro_replay.db import ensure_db, record_chart
from macro_replay.charts import render_chart
from macro_replay.config import load_mcp_servers
from macro_replay.pipeline import process_indicator
from macro_replay.source_labels import display_source_label
from scripts.rerender_charts import load_chart_frame


def main() -> None:
    try:
        servers = load_mcp_servers()
    except FileNotFoundError:
        servers = {}

    conn = ensure_db()
    try:
        for indicator in load_indicators():
            chart_type = (indicator.chart or {}).get("type")
            if chart_type == "term_structure" or indicator.source in {"manual-excel-term", "manual-excel-line"}:
                process_indicator(indicator, servers)
                print(f"[INFO] Re-rendered {indicator.id} via pipeline")
                continue

            df = load_chart_frame(conn, indicator)
            if df is None or df.empty:
                print(f"[WARN] Skipped {indicator.id}: no observations")
                continue
            html_path, image_path = render_chart(df, indicator, Path("charts") / indicator.id, write_image=True)
            record_chart(conn, indicator.id, html_path, image_path, display_source_label(indicator.source))
            print(f"[INFO] Re-rendered {indicator.id}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
