from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def refresh(trade_date: str, report_date: str, only: str = "all") -> dict:
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "success": [], "failed": [], "skipped": []}
    report["skipped"].append({
        "reason": "valuation and financial cross-sectional indicators were removed from the dashboard catalog",
    })
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    Path("logs").mkdir(exist_ok=True)
    Path("logs/ifind_valuation_quantiles_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh iFinD valuation quantiles and remaining cross-sectional fields")
    parser.add_argument("--trade-date", default="2026-07-16")
    parser.add_argument("--report-date", default="2025-12-31")
    parser.add_argument("--only", choices=("all", "pe", "pb", "remaining"), default="all")
    args = parser.parse_args()
    print(json.dumps(refresh(args.trade_date, args.report_date, args.only), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
