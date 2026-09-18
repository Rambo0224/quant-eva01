from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datahub.adapters.baostock_equity import (
    SOURCE_ID,
    BaoStockClient,
    BaoStockUnavailable,
    cached_universe_path,
    normalize_symbol,
)
from macro_replay.db import ensure_db, insert_missing_equity_bars, insert_missing_equity_bars_bulk


REQUEST_BUDGET_PATH = Path("data") / "baostock_request_budget.json"
TRADE_DATE_CACHE_PATH = Path("data") / "baostock_trade_dates.csv"
DAILY_HISTORY_CACHE_PATH = Path("data") / "baostock_daily_history_dates.json"


def _excluded_symbols() -> set[str]:
    """Project-level equity exclusions, shared with the market sync service."""
    from datahub.sync.storage import settings

    return {
        str(symbol).split('.')[0].zfill(6)
        for symbol in settings().get('market', {}).get('excluded_symbols', [])
    }


def _load_daily_history_cache() -> set[str]:
    if not DAILY_HISTORY_CACHE_PATH.exists():
        return set()
    try:
        payload = json.loads(DAILY_HISTORY_CACHE_PATH.read_text(encoding="utf-8"))
        return {str(value) for value in payload.get("successful_dates", [])}
    except (OSError, TypeError, ValueError):
        return set()


def _save_daily_history_cache(dates: set[str]) -> None:
    DAILY_HISTORY_CACHE_PATH.parent.mkdir(exist_ok=True)
    DAILY_HISTORY_CACHE_PATH.write_text(
        json.dumps({"successful_dates": sorted(dates)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _verified_complete_day(conn, source_id: str, day: str) -> bool:
    manifest=conn.execute('SELECT symbols FROM raw.daily_market_manifest WHERE source_id=? AND trade_date=?',[source_id,day]).fetchone()
    if not manifest:
        return False
    expected=set(json.loads(manifest[0])) - _excluded_symbols()
    saved={r[0] for r in conn.execute('SELECT DISTINCT symbol FROM equity_daily_bars WHERE source_id=? AND obs_time::DATE=?',[source_id,day]).fetchall()}
    return bool(expected) and expected.issubset(saved)


def _load_trade_date_cache() -> pd.DataFrame:
    if not TRADE_DATE_CACHE_PATH.exists():
        return pd.DataFrame(columns=["calendar_date", "is_trading_day"])
    try:
        frame = pd.read_csv(TRADE_DATE_CACHE_PATH, dtype={"is_trading_day": "Int64"})
        frame["calendar_date"] = pd.to_datetime(frame["calendar_date"], errors="coerce").dt.normalize()
        frame["is_trading_day"] = pd.to_numeric(frame["is_trading_day"], errors="coerce")
        return frame.dropna(subset=["calendar_date"]).drop_duplicates("calendar_date")
    except (OSError, ValueError, TypeError):
        return pd.DataFrame(columns=["calendar_date", "is_trading_day"])


def _save_trade_date_cache(frame: pd.DataFrame) -> None:
    TRADE_DATE_CACHE_PATH.parent.mkdir(exist_ok=True)
    frame.sort_values("calendar_date").drop_duplicates("calendar_date").to_csv(
        TRADE_DATE_CACHE_PATH, index=False, date_format="%Y-%m-%d"
    )


def _reserve_request(budget: "DailyRequestBudget", last_request_at: float, interval: float) -> float:
    elapsed = time.monotonic() - last_request_at
    if elapsed < interval:
        time.sleep(interval - elapsed)
    budget.reserve()
    return time.monotonic()


def _fetch_daily_history_with_retry(
    client: BaoStockClient,
    date: str,
    *,
    retries: int = 3,
    sleep_seconds: float = 4.0,
) -> object:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return client.fetch_daily_history_all(date)
        except BaoStockUnavailable as exc:
            last_error = exc
            message = str(exc)
            transient = any(token in message for token in ("10002007", "网络接收错误", "network", "timeout", "timed out"))
            if attempt >= retries or not transient:
                raise
            time.sleep(sleep_seconds * attempt)
            # A timed-out response leaves the TCP stream out of sync. Reusing
            # that stream can contaminate every subsequent day's response.
            client.__exit__(None,None,None)
            client.__enter__()
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"failed to fetch BaoStock daily history for {date}")


class DailyRequestBudget:
    def __init__(self, limit: int = 20_000, path: Path = REQUEST_BUDGET_PATH):
        self.limit = int(limit)
        self.path = path
        self.day = datetime.now().date().isoformat()
        self.used = 0
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("day") == self.day:
                    self.used = int(payload.get("used", 0))
            except (OSError, ValueError, TypeError):
                self.used = 0

    def reserve(self, count: int = 1) -> None:
        if self.used + count > self.limit:
            raise RuntimeError(f"BaoStock daily request budget exhausted: {self.used}/{self.limit}")
        self.used += count
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(
            json.dumps({"day": self.day, "used": self.used, "limit": self.limit}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _load_or_fetch_universe(client: BaoStockClient, day: str, refresh: bool) -> pd.DataFrame:
    path = cached_universe_path()
    if path.exists() and not refresh:
        cached = pd.read_csv(path, dtype={"symbol": "string", "exchange_code": "string"})
        if "ipo_date" in cached.columns:
            return cached[~cached['symbol'].astype(str).isin(_excluded_symbols())].copy()
    frame = client.fetch_universe(day)
    frame = frame[~frame['symbol'].astype(str).isin(_excluded_symbols())].copy()
    path.parent.mkdir(exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    metadata = getattr(client,'security_metadata',None)
    if metadata is not None:
        is_excluded_equity = (
            metadata['type'].astype(str).eq('1')
            & metadata['code'].astype(str).str.split('.').str[-1].isin(_excluded_symbols())
        )
        metadata = metadata[~is_excluded_equity]
        metadata.assign(verified_on=day).to_csv(path.with_name('baostock_security_status.csv'),index=False,encoding='utf-8-sig')
    return frame


def _bounds(conn, symbol: str, source_id: str = SOURCE_ID):
    """Return bounds covered by this provider only.

    Another provider's rows cannot suppress a BaoStock request: source-level
    coverage keeps refreshes incremental while preserving provenance.
    """
    row = conn.execute(
        """
        SELECT min(obs_time), max(obs_time)
        FROM equity_daily_bars
        WHERE source_id = ?
          AND regexp_replace(symbol, '\\.(SS|SZ|BJ)$', '') = ?
        """,
        [source_id, symbol],
    ).fetchone()
    if not row or row[0] is None:
        return None
    return pd.Timestamp(row[0]).normalize(), pd.Timestamp(row[1]).normalize()


def refresh_daily_all(
    start: str,
    end: str,
    universe_day: str,
    symbols: list[str] | None = None,
    adjustflag: str = "3",
    refresh_universe: bool = False,
    request_interval: float = 0.3,
    daily_limit: int = 20_000,
) -> dict:
    """Fetch the full A-share market one trading day at a time.

    BaoStock returns the entire A-share market in one response for this API.
    Successful dates are checkpointed so an interrupted run resumes without
    repeating completed requests.
    """
    if str(adjustflag) != '3':
        raise ValueError('Daily-all endpoint supplies unadjusted prices only')
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_id": SOURCE_ID,
        "mode": "daily_all",
        "requested_start": pd.Timestamp(start).date().isoformat(),
        "requested_end": pd.Timestamp(end).date().isoformat(),
        "success": [],
        "failed": [],
        "skipped": [],
        "request_interval_seconds": request_interval,
        "daily_request_limit": daily_limit,
    }
    conn = ensure_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS raw.daily_market_manifest
        (source_id VARCHAR, trade_date DATE, symbols JSON, PRIMARY KEY(source_id,trade_date))''')
    budget = DailyRequestBudget(daily_limit)
    excluded_symbols = _excluded_symbols()
    run_requests = 0
    last_request_at = 0.0
    universe = pd.DataFrame()
    try:
        with BaoStockClient(adjustflag=adjustflag,read_timeout=60) as client:
            cache_has_ipo = False
            cache_path = cached_universe_path()
            if cache_path.exists() and not refresh_universe:
                cache_has_ipo = "ipo_date" in pd.read_csv(cache_path, nrows=0).columns
            if refresh_universe or not cache_has_ipo:
                last_request_at = _reserve_request(budget, last_request_at, request_interval)
                last_request_at = _reserve_request(budget, last_request_at, request_interval)
                run_requests += 2
            universe = _load_or_fetch_universe(client, universe_day, refresh_universe)

            start_ts = pd.Timestamp(start).normalize()
            end_ts = pd.Timestamp(end).normalize()
            trade_dates = _load_trade_date_cache()
            missing_ranges = []
            if trade_dates.empty:
                missing_ranges.append((start_ts, end_ts))
            else:
                cache_min = trade_dates["calendar_date"].min()
                cache_max = trade_dates["calendar_date"].max()
                if start_ts < cache_min:
                    missing_ranges.append((start_ts, cache_min - pd.Timedelta(days=1)))
                if end_ts > cache_max:
                    missing_ranges.append((cache_max + pd.Timedelta(days=1), end_ts))
            for range_start, range_end in missing_ranges:
                if range_start > range_end:
                    continue
                last_request_at = _reserve_request(budget, last_request_at, request_interval)
                fetched_dates = client.fetch_trade_dates(
                    range_start.date().isoformat(), range_end.date().isoformat()
                )
                run_requests += 1
                if fetched_dates.empty:
                    raise RuntimeError(f"BaoStock returned no trade dates for {range_start.date()} to {range_end.date()}")
                fetched_dates = fetched_dates.rename(columns={"calendar_date": "calendar_date"})
                fetched_dates = fetched_dates[["calendar_date", "is_trading_day"]].copy()
                fetched_dates["calendar_date"] = pd.to_datetime(
                    fetched_dates["calendar_date"], errors="coerce"
                ).dt.normalize()
                trade_dates = pd.concat([trade_dates, fetched_dates], ignore_index=True)
                trade_dates = trade_dates.dropna(subset=["calendar_date"]).drop_duplicates(
                    "calendar_date", keep="last"
                )
                trade_dates["calendar_date"] = pd.to_datetime(
                    trade_dates["calendar_date"], errors="coerce"
                ).dt.normalize()
                _save_trade_date_cache(trade_dates)

            requested_dates = (
                trade_dates.loc[
                    trade_dates["calendar_date"].between(start_ts, end_ts)
                    & trade_dates["is_trading_day"].astype(str).isin({"1", "1.0"}),
                    "calendar_date",
                ]
                .dt.strftime("%Y-%m-%d")
                .tolist()
            )
            completed_dates = _load_daily_history_cache()
            previous=conn.execute("SELECT report FROM raw.sync_state WHERE rule_id='baostock_stock'").fetchone()
            if previous:
                pending=json.loads(previous[0]).get('result',{}).get('failed',[])
                requested_dates=sorted(set(requested_dates)|{r['date'] for r in pending if r.get('date') and r['date']<=end})
            requested_symbols = {
                normalize_symbol(value) for value in symbols
            } if symbols else None
            consecutive_failures = 0
            requested_dates = sorted(requested_dates, reverse=True)
            for index, date in enumerate(requested_dates, start=1):
                if _verified_complete_day(conn,SOURCE_ID,date):
                    report["skipped"].append({"date": date, "reason": "date already fetched by BaoStock daily-all API"})
                    continue
                try:
                    if date in completed_dates and requested_symbols is None:
                        # Upgrade a legacy date-only checkpoint using the official
                        # historical trading universe, never an approximate row count.
                        historical=client.fetch_universe(date)
                        active=set(historical.loc[historical['tradeStatus'].astype(str).eq('1'),'symbol'].astype(str)) - excluded_symbols
                        stored={r[0] for r in conn.execute('SELECT DISTINCT symbol FROM equity_daily_bars WHERE source_id=? AND obs_time::DATE=? AND open>0 AND close>0 AND high>=low',[SOURCE_ID,date]).fetchall()}
                        if active and active.issubset(stored):
                            conn.execute('INSERT OR REPLACE INTO raw.daily_market_manifest VALUES (?,?,?)',[SOURCE_ID,date,json.dumps(sorted(active))])
                            report['skipped'].append({'date':date,'reason':'Legacy checkpoint verified against official historical active universe and stored prices','symbols':len(active)})
                            continue
                    last_request_at = _reserve_request(budget, last_request_at, request_interval)
                    result = _fetch_daily_history_with_retry(client, date)
                    run_requests += 1
                    frame = result.frame
                    if frame.empty or not pd.to_datetime(frame['date']).eq(pd.Timestamp(date)).all():
                        raise ValueError('Daily response is empty or contains a different trading date')
                    full_symbols=sorted(set(frame['symbol'].astype(str)) - excluded_symbols)
                    frame = frame[~frame['symbol'].astype(str).isin(excluded_symbols)]
                    if requested_symbols is not None:
                        frame = frame[frame["symbol"].astype(str).isin(requested_symbols)]
                    inserted = insert_missing_equity_bars_bulk(conn, result.source_id, frame)
                    if date == universe_day and requested_symbols is None:
                        active = set(universe.loc[universe['tradeStatus'].astype(str).eq('1'),'symbol'].astype(str)) - excluded_symbols
                        missing = active-set(full_symbols)
                        if missing:
                            raise ValueError(f'Daily response missing {len(missing)} active symbols: {sorted(missing)}; valid rows were saved')
                    if requested_symbols is None:
                        conn.execute('INSERT OR REPLACE INTO raw.daily_market_manifest VALUES (?,?,?)',[SOURCE_ID,date,json.dumps(full_symbols)])
                        completed_dates.add(date)
                        _save_daily_history_cache(completed_dates)
                    report["success"].append({
                        "date": date,
                        "fetched_rows": len(frame),
                        "inserted_rows": inserted,
                    })
                    consecutive_failures = 0
                except Exception as exc:
                    report["failed"].append({"date": date, "error": str(exc)})
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        report["stopped_reason"] = "three consecutive BaoStock daily-all failures; stopped to protect the account"
                        report['failed'].extend({'date':day,'error':'not attempted after consecutive failures'} for day in requested_dates[index:])
                        break
                report['completed_dates']=index
                Path('logs').mkdir(exist_ok=True)
                Path('logs/baostock_equity_refresh.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(f"[INFO] BaoStock daily history {date}: {index}/{len(requested_dates)}; failed={len(report['failed'])}", flush=True)
            report["api_requests_run"] = run_requests
            report["api_requests"] = budget.used
    except Exception as exc:
        report["failed"].append({"error": str(exc)})
        report["stopped_reason"] = "BaoStock connection unavailable; refresh aborted without fabricating data"
        report["api_requests_run"] = run_requests
        report["api_requests"] = budget.used
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["universe_rows"] = len(universe)
        Path("logs").mkdir(exist_ok=True)
        Path("logs/baostock_equity_refresh.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise
    finally:
        conn.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["universe_rows"] = len(universe)
    report["requested_dates"] = len(requested_dates) if "requested_dates" in locals() else 0
    Path("logs").mkdir(exist_ok=True)
    Path("logs/baostock_equity_refresh.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def refresh(
    start: str,
    end: str,
    universe_day: str,
    symbols: list[str] | None = None,
    max_symbols: int | None = None,
    adjustflag: str = "3",
    refresh_universe: bool = False,
    request_interval: float = 0.5,
    daily_limit: int = 20_000,
) -> dict:
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_id": SOURCE_ID,
        "success": [],
        "failed": [],
        "skipped": [],
        "request_interval_seconds": request_interval,
        "daily_request_limit": daily_limit,
    }
    conn = ensure_db()
    budget = DailyRequestBudget(daily_limit)
    last_request_at = 0.0
    try:
        with BaoStockClient(adjustflag=adjustflag) as client:
            cache_has_ipo = False
            cache_path = cached_universe_path()
            if cache_path.exists() and not refresh_universe:
                cache_has_ipo = "ipo_date" in pd.read_csv(cache_path, nrows=0).columns
            universe_refresh_needed = refresh_universe or not cache_has_ipo
            if universe_refresh_needed:
                elapsed = time.monotonic() - last_request_at
                if elapsed < request_interval:
                    time.sleep(request_interval - elapsed)
                budget.reserve(2)
                last_request_at = time.monotonic()
            universe = _load_or_fetch_universe(client, universe_day, refresh_universe)
            excluded_symbols = _excluded_symbols()
            requested = [normalize_symbol(value) for value in (symbols or universe["symbol"].astype(str).tolist())]
            removed = sorted(set(requested) & excluded_symbols)
            requested = [symbol for symbol in requested if symbol not in excluded_symbols]
            report['skipped'].extend({'symbol': symbol, 'reason': 'excluded_by_project'} for symbol in removed)
            if max_symbols is not None:
                requested = requested[:max_symbols]
            start_ts, end_ts = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
            consecutive_failures = 0
            for index, symbol in enumerate(requested, start=1):
                exchange_code = universe.loc[universe["symbol"].astype(str).eq(symbol), "exchange_code"]
                if exchange_code.empty:
                    report["failed"].append({"symbol": symbol, "error": "symbol is not in BaoStock cached universe"})
                    continue
                code = str(exchange_code.iloc[0])
                bounds = _bounds(conn, symbol)
                ipo_value = universe.loc[universe["symbol"].astype(str).eq(symbol), "ipo_date"].iloc[0]
                ipo_date = pd.Timestamp(ipo_value).normalize() if pd.notna(ipo_value) and str(ipo_value).strip() else None
                effective_start = max(start_ts, ipo_date) if ipo_date is not None else start_ts
                if bounds is None:
                    left, right = effective_start, end_ts
                elif effective_start < bounds[0]:
                    left, right = effective_start, bounds[0] - pd.Timedelta(days=1)
                elif end_ts > bounds[1]:
                    left, right = bounds[1] + pd.Timedelta(days=1), end_ts
                else:
                    report["skipped"].append({"symbol": symbol, "reason": "requested date bounds already exist"})
                    continue
                if left > right:
                    report["skipped"].append({"symbol": symbol, "reason": "no valid trading-date gap"})
                    continue
                try:
                    elapsed = time.monotonic() - last_request_at
                    if elapsed < request_interval:
                        time.sleep(request_interval - elapsed)
                    budget.reserve()
                    last_request_at = time.monotonic()
                    result = client.fetch_history(code, left.date().isoformat(), right.date().isoformat())
                    inserted = insert_missing_equity_bars(conn, result.source_id, result.frame)
                    report["success"].append({"symbol": symbol, "code": code, "fetched_rows": len(result.frame), "inserted_rows": inserted})
                    consecutive_failures = 0
                except Exception as exc:
                    report["failed"].append({"symbol": symbol, "code": code, "error": str(exc)})
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        report["stopped_reason"] = "three consecutive BaoStock history failures; stopped to protect the account"
                        break
                if index % 25 == 0 or index == len(requested):
                    print(f"[INFO] BaoStock history progress {index}/{len(requested)}", flush=True)
            report["api_requests"] = budget.used
    except Exception as exc:
        # Persist connection/login failures as a refresh report so the next
        # run can distinguish an unavailable provider from an empty result.
        report["failed"].append({"error": str(exc)})
        report["stopped_reason"] = "BaoStock connection unavailable; refresh aborted without fabricating data"
        report["api_requests"] = budget.used
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        Path("logs").mkdir(exist_ok=True)
        Path("logs/baostock_equity_refresh.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise
    finally:
        conn.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["universe_rows"] = len(universe) if "universe" in locals() else 0
    report["requested_symbols"] = len(requested) if "requested" in locals() else 0
    Path("logs").mkdir(exist_ok=True)
    Path("logs/baostock_equity_refresh.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Incrementally fetch A-share daily history from BaoStock")
    parser.add_argument("--mode", choices=("daily-all", "symbol"), default="daily-all")
    parser.add_argument("--start", default="2021-01-01", help="Earliest requested date for the dashboard history")
    parser.add_argument("--end", default=(pd.Timestamp.today().normalize() - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), help="Latest completed calendar day; today's incomplete session is excluded")
    parser.add_argument("--universe-day", default=None)
    parser.add_argument("--symbols", default="", help="BaoStock codes or six-digit codes, comma-separated")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--adjustflag", choices=("1", "2", "3"), default="3")
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--request-interval", type=float, default=0.3, help="Minimum seconds between BaoStock API calls")
    parser.add_argument("--daily-limit", type=int, default=20_000, help="Hard daily BaoStock request budget")
    args = parser.parse_args()
    universe_day = args.universe_day or args.end
    symbols = [value.strip().lower() for value in args.symbols.split(",") if value.strip()] or None
    if args.mode == "daily-all":
        result = refresh_daily_all(
            args.start,
            args.end,
            universe_day,
            symbols,
            args.adjustflag,
            args.refresh_universe,
            args.request_interval,
            args.daily_limit,
        )
    else:
        result = refresh(
            args.start,
            args.end,
            universe_day,
            symbols,
            args.max_symbols,
            args.adjustflag,
            args.refresh_universe,
            args.request_interval,
            args.daily_limit,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
