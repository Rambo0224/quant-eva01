# Open-Source Data Adapters

OmniSignal treats the upstream projects differently:

- `OpenBB`: optional `openbb-yfinance` provider for public single-symbol A-share OHLCV. The adapter maps `600000` to `600000.SS` and `000001` to `000001.SZ`.
- `Tushare`: optional `tushare` Pro client for token-authenticated daily OHLCV and bounded all-market batches. Set `TUSHARE_TOKEN`; without it, the adapter fails explicitly.
- `BaoStock`: sequential A-share daily market-history source. The OmniSignal adapter requests only OHLCV, amount, turnover, trading status, percentage change and ST fields. It uses one login, no concurrency, a 0.5-second default request interval, and a persistent 20,000-request daily budget.
- `AKQuant`: backtest and data-feed normalization framework, not a market-data provider. Its `DataFeedAdapter` OHLCV contract informed the raw-bar schema but it is not queried for data.

Fetch BaoStock history with the guarded sequential adapter:

```powershell
.\\.venv-win\\Scripts\\python.exe scripts\\fetch_baostock_equity_bars.py `
  --start 2020-01-01 `
  --end 2025-07-18 `
  --request-interval 0.5 `
  --daily-limit 20000
```

Install the optional providers with:

```powershell
.\.venv-win\Scripts\python.exe -m pip install -e ".[providers]"
```

Fetch two real A-share daily series through OpenBB and store only missing source/symbol/date rows:

```powershell
.\.venv-win\Scripts\python.exe scripts\fetch_equity_bars.py `
  --symbols 600000,000001 `
  --start 2025-01-01 `
  --end 2025-01-10 `
  --source openbb
```

The raw rows are stored in `equity_daily_bars`. The script uses the official AkShare trading calendar for boundaries, so weekends and holidays are not repeatedly requested. Tushare uses the same storage path after a token is configured:

```powershell
$env:TUSHARE_TOKEN = "your-token"
.\.venv-win\Scripts\python.exe scripts\fetch_equity_bars.py `
  --symbols 600000.SH,000001.SZ `
  --start 2025-01-01 `
  --end 2025-01-10 `
  --source tushare
```

These providers are candidates for raw A-share factor inputs. They do not replace validated production series until the returned coverage, adjustment convention, and point-in-time behavior have been checked.
