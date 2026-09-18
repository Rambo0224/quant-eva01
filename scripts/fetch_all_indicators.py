#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from macro_replay.dashboard_service import refresh_all_dashboard


def clean_full_refresh_outputs() -> None:
    db_path = PROJECT_ROOT / "data" / "replay.duckdb"
    charts_dir = PROJECT_ROOT / "charts"

    if db_path.exists():
        db_path.unlink()

    if charts_dir.exists():
        for pattern in ("*.html", "*.png"):
            for asset in charts_dir.rglob(pattern):
                asset.unlink()

        # Remove empty directories left behind by deleted chart assets.
        for folder in sorted(charts_dir.rglob("*"), reverse=True):
            if folder.is_dir():
                try:
                    folder.rmdir()
                except OSError:
                    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh all indicators and charts.")
    parser.add_argument(
        "--mode",
        choices=("incremental", "full"),
        default="incremental",
        help="incremental: only backfill recent changes based on the database; full: rebuild the local database and chart outputs from scratch.",
    )
    args = parser.parse_args()

    if args.mode == "full":
        print("[INFO] Full refresh selected: rebuilding database and chart outputs from scratch.")
        clean_full_refresh_outputs()
    else:
        print("[INFO] Incremental refresh selected: only updating the latest changed slices.")

    report = refresh_all_dashboard()
    failed_steps = [step for step in report.get("steps", []) if step.get("status") == "failed"]
    if failed_steps:
        print(f"[ERROR] Dashboard refresh finished with {len(failed_steps)} failed step(s).")
        print("[ERROR] See logs/dashboard_refresh_all.json for details.")
    else:
        print("[INFO] Dashboard refresh complete.")
    print(report)
    if failed_steps:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
