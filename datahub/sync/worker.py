"""Isolated source-module request. stdout is a single pandas table JSON document."""
import contextlib
import sys

sys.stderr.reconfigure(encoding='utf-8')
kind,symbol,start,end=sys.argv[1:]
with contextlib.redirect_stdout(sys.stderr):
    if kind=='calendar':
        from datahub.adapters.baostock_equity import BaoStockClient
        with BaoStockClient() as client:
            frame=client.fetch_trade_dates(start,end)
    else:
        from .storage import settings
        from .source_registry import fetch
        source=settings()['sources'].get(kind)
        if source is None or 'adapter' not in source:
            raise ValueError(f'Unknown configured market source: {kind}')
        frame=fetch(source['adapter'],symbol,start,end)
sys.stdout.reconfigure(encoding='utf-8')
print(frame.to_json(orient='table',date_format='iso'))
