"""Compatibility CLI for the independent data acquisition module."""
from __future__ import annotations

import argparse
import json
import pandas as pd
from datahub.adapters.akshare_equity import fetch_a_share_universe


def refresh(start, end, symbols=None, max_workers=4, max_symbols=None, adjust="qfq", batch_size=100):
    """Compatibility entry: use the shared audited acquisition module."""
    if adjust != 'qfq':
        raise ValueError('This dataset is forward adjusted. Configure a distinct dataset for another adjustment.')
    from datahub.sync.service import run_rule
    if max_symbols is not None:
        symbols = (symbols or fetch_a_share_universe()['code'].tolist())[:max_symbols]
    return run_rule('akshare_stock',end,symbols)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Incrementally fetch full A-share daily bars from AkShare")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=(pd.Timestamp.today() - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    parser.add_argument("--symbols", default="", help="Optional comma-separated stock codes")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--adjust", choices=("", "qfq", "hfq"), default="qfq")
    args = parser.parse_args()
    symbols = [value.strip() for value in args.symbols.split(",") if value.strip()] or None
    print(json.dumps(refresh(args.start, args.end, symbols, args.max_workers, args.max_symbols, args.adjust, args.batch_size), ensure_ascii=False, indent=2))
