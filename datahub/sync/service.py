from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

from .storage import ROOT, finish_run, initialize, publish, settings, start_run, store_frame
from .planning import plan_market_update
from .source_registry import validate_market_sources


def excluded_stock_symbols(config):
    """Return normalized A-share codes excluded from this research universe."""
    return {str(symbol).split('.')[0].zfill(6) for symbol in config.get('excluded_symbols', [])}


def inactive_symbols(end):
    result={}
    path=ROOT/'data'/'baostock_universe.csv'
    if path.exists():
        for row in pd.read_csv(path,dtype=str).fillna('').to_dict('records'):
            if row.get('universe_date')==end and row.get('tradeStatus')=='0':
                result[row['symbol']]='verified_suspended'
    path=ROOT/'data'/'baostock_security_status.csv'
    if path.exists():
        from datahub.adapters.baostock_equity import is_a_share_code
        for row in pd.read_csv(path,dtype=str).fillna('').to_dict('records'):
            if row.get('verified_on')==end and row.get('outDate') and row['outDate']<=end and is_a_share_code(row['code']):
                result[row['code'].split('.')[-1]]='verified_delisted'
    return result


def default_end(now=None):
    """Last completed domestic session, using the project's official calendar."""
    now = pd.Timestamp.now(tz='Asia/Shanghai') if now is None else pd.Timestamp(now)
    day = now.date()
    if now.hour < 15:
        day -= pd.Timedelta(days=1)
    path = ROOT / 'data' / 'baostock_trade_dates.csv'
    if not path.exists() or pd.to_datetime(pd.read_csv(path)['calendar_date']).max().date() < day:
        try:
            calendar=fetch_worker('calendar','all',f'{day.year}-01-01',f'{day.year}-12-31',15)
        except Exception as primary_error:
            from datahub.adapters.akshare_public import fetch_trade_dates
            try:
                dates=fetch_trade_dates(f'{day.year}-01-01',f'{day.year}-12-31')
            except Exception as fallback_error:
                raise RuntimeError('Official trade calendar could not be refreshed from BaoStock or AkShare') from fallback_error
            if dates.empty:
                raise RuntimeError('AkShare trade calendar returned no trading dates') from primary_error
            calendar=pd.DataFrame({'calendar_date':dates,'is_trading_day':1})
        if path.exists():
            calendar=pd.concat([pd.read_csv(path),calendar],ignore_index=True)
        calendar['calendar_date']=pd.to_datetime(calendar['calendar_date']).dt.strftime('%Y-%m-%d')
        path.parent.mkdir(exist_ok=True)
        calendar.drop_duplicates('calendar_date',keep='last').sort_values('calendar_date').to_csv(path,index=False)
    if path.exists():
        calendar = pd.read_csv(path)
        dates = pd.to_datetime(calendar['calendar_date'])
        if dates.max().date() >= day:
            valid = dates[(dates.dt.date <= day) & calendar['is_trading_day'].astype(str).isin(['1','1.0'])]
            if not valid.empty:
                return valid.max().date().isoformat()
    raise RuntimeError('Official trade calendar does not cover today; refresh calendar or specify --end explicitly')


def fetch_worker(source_id, symbol, start, end, timeout=45):
    """A hard per-request deadline also bounds libraries without HTTP timeouts."""
    import os
    process = subprocess.Popen([sys.executable,'-m','datahub.sync.worker',source_id,symbol,start,end],
                            cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace')
    try:
        stdout,stderr=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Windows venv launchers spawn the actual interpreter: kill the whole tree.
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
        else: process.kill()
        process.communicate()
        raise TimeoutError(f'{source_id} {symbol}: request exceeded {timeout}s')
    if process.returncode:
        raise RuntimeError(stderr[-2000:])
    from io import StringIO
    return pd.read_json(StringIO(stdout),orient='table')


def sync_market(end=None, symbols=None, kinds=('stock','etf','index'), conn=None, fetcher=None):
    from macro_replay.db import ensure_db
    own = conn is None
    conn = conn or ensure_db()
    fetcher = fetcher or fetch_worker
    configuration = settings()
    validate_market_sources(configuration)
    config = configuration['market']
    excluded_stocks = excluded_stock_symbols(config)
    source_specs = configuration['sources']
    end = end or default_end()
    report = {'success':[],'failed':[],'skipped':[],'end':end}
    try:
        initialize(conn)
        tasks=[]
        candidate_sources={kind:[candidate['source'] for candidate in candidates]
                           for kind,candidates in config['candidates'].items()}
        plan_summary={'standardized_current':0,'raw_ready_for_standardization':0,'raw_data_missing':0,'fetch_windows':0}
        def queue_if_raw_missing(kind,symbol,configured_start,adjustment,factor=None):
            plan=plan_market_update(conn,kind,symbol,end,configured_start,adjustment,factor)
            plan_summary[plan.status]+=1
            if not plan.fetch_windows:
                report['skipped'].append({'symbol':symbol,'kind':kind,'reason':plan.status,
                                          'standardized_missing_dates':len(plan.standardized_missing_dates)})
            else:
                plan_summary['fetch_windows']+=len(plan.fetch_windows)
                for request_start,request_end in plan.fetch_windows:
                    tasks.append((kind,symbol,request_start,request_end,adjustment,factor))
        if 'stock' in kinds:
            if symbols is not None:
                stocks=[]
                for symbol in symbols:
                    code=str(symbol).split('.')[0].zfill(6)
                    if code in excluded_stocks:
                        report['skipped'].append({'symbol':code,'kind':'stock','reason':'excluded_by_project'})
                    else:
                        stocks.append(code)
            elif config['stock_scope']=='all':
                from datahub.adapters.akshare_equity import fetch_a_share_universe
                stocks=fetch_a_share_universe()['code'].tolist()
            else:
                marks=','.join('?' for _ in candidate_sources['stock'])
                stocks=[r[0] for r in conn.execute(f"SELECT DISTINCT symbol FROM equity_daily_bars WHERE source_id IN ({marks}) UNION SELECT symbol FROM raw.selected_series WHERE kind='stock' ORDER BY symbol",candidate_sources['stock']).fetchall()]
                if not stocks:
                    raise ValueError('No tracked stocks; supply --symbols or configure stock_scope: all')
            stocks=[str(symbol).split('.')[0].zfill(6) for symbol in stocks if str(symbol).split('.')[0].zfill(6) not in excluded_stocks]
            for symbol in stocks:
                queue_if_raw_missing('stock',symbol,config['stock_start'],'qfq')
        if 'etf' in kinds:
            from macro_replay.etf_catalog import ETF_WATCHLIST
            marks=','.join('?' for _ in candidate_sources['etf'])
            existing = {r[0] for r in conn.execute(f"SELECT DISTINCT symbol FROM equity_daily_bars WHERE source_id IN ({marks})",candidate_sources['etf']).fetchall()}
            etfs = existing | {str(item['code']).zfill(6) for item in ETF_WATCHLIST}
            for item in [{'code':code} for code in sorted(etfs)]:
                symbol=str(item['code']).zfill(6)
                if symbols is not None and symbol not in symbols:
                    continue
                queue_if_raw_missing('etf',symbol,config['etf_start'],'raw')
        if 'index' in kinds:
            for factor,symbol in config['indexes'].items():
                queue_if_raw_missing('index',symbol,config['index_start'],'raw',factor)
        circuit = {}
        inactive=inactive_symbols(end)
        # Market providers are single-symbol APIs. Fetch primary candidates in a
        # bounded pool, then retain a single writer for all DuckDB mutations.
        primary_results={}
        if fetcher is fetch_worker and tasks:
            def fetch_primary(task):
                kind,symbol,start,request_end,adjustment,factor=task
                source=candidate_sources[kind][0]
                try:
                    return task,fetcher(source,symbol,start,request_end,config['timeout_seconds'])
                except Exception as exc:
                    return task,exc
            with ThreadPoolExecutor(max_workers=max(1,int(config.get('max_parallel_requests',4)))) as executor:
                futures=[executor.submit(fetch_primary,task) for task in tasks]
                for future in as_completed(futures):
                    task,outcome=future.result()
                    primary_results[task]=outcome
        for kind,symbol,start,request_end,adjustment,factor in tasks:
            if kind=='stock' and symbol in inactive:
                report['success'].append({'symbol':symbol,'status':inactive[symbol],'reason':'Official status verifies no current-day trading bar is due'})
                continue
            previous=conn.execute('SELECT source_id,latest_date FROM raw.selected_series WHERE kind=? AND symbol=? AND requested_end=?',[kind,symbol,request_end]).fetchone()
            if previous and str(previous[1])==request_end and kind!='index':
                actual=conn.execute('SELECT max(obs_time)::DATE FROM equity_daily_bars WHERE source_id=? AND symbol=?',[previous[0],symbol]).fetchone()[0]
                if actual and str(actual)>=request_end:
                    report['success'].append({'symbol':symbol,'source':previous[0],'latest':request_end,'reason':'aligned raw batch already stored'})
                    continue
            attempts=[]
            selected=None
            for source in candidate_sources[kind]:
                source_spec=source_specs[source]
                candidate_start=start
                request={'kind':kind,'symbol':symbol,'start':candidate_start,'end':request_end,'adjustment':adjustment}
                run=start_run(conn,source,request)
                fetched=False
                try:
                    if source_spec.get('adjustment', adjustment) != adjustment:
                        raise ValueError('Candidate price adjustment does not match dataset')
                    if circuit.get(source,0)>=3 and source != config['candidates'][kind][-1]['source']:
                        raise RuntimeError('Source circuit open after 3 failed batches; next candidate will be tried')
                    if candidate_start>request_end:
                        raise ValueError('Requested end precedes source history')
                    primary_key=(kind,symbol,start,request_end,adjustment,factor)
                    cached=primary_results.pop(primary_key,None) if source==candidate_sources[kind][0] else None
                    if cached is not None:
                        if isinstance(cached,Exception):
                            raise cached
                        frame=cached
                    else:
                        for attempt in range(config['retries']):
                            try:
                                frame=fetcher(source,symbol,candidate_start,request_end,config['timeout_seconds'])
                                break
                            except Exception:
                                if attempt+1==config['retries']:raise
                                if fetcher is fetch_worker:time.sleep(2*(attempt+1))
                    fetched=True
                    if 'price_adjustment' in frame:
                        actual=frame['price_adjustment'].astype(str).replace({'1':'hfq','2':'qfq','3':'raw','none':'raw','':'raw'})
                        if not actual.eq(adjustment).all():
                            raise ValueError('Returned adjustment differs from requested dataset')
                    dates=pd.to_datetime(frame['date'],errors='coerce')
                    if frame.empty or dates.isna().any() or dates.max().date().isoformat()!=request_end:
                        conn.execute('INSERT INTO raw.acquisition_batches(run_id,source_id,symbol,request,records) VALUES (?,?,?,?,?)',
                            [run,source,symbol,json.dumps(request),frame.to_json(orient='records',date_format='iso')])
                        # A delayed but valid response can still advance the raw/core
                        # data. It does not satisfy readiness and fallback continues.
                        if not frame.empty and not dates.isna().any() and dates.max()<=pd.Timestamp(request_end):
                            if kind=='index':
                                store_index(conn,run,source,factor,frame,request)
                            else:
                                store_frame(conn,run,source,symbol,frame,request,adjustment,kind)
                        raise ValueError(f'Response not aligned to {request_end}; latest={dates.max()}')
                    if kind=='index':
                        written=store_index(conn,run,source,factor,frame,request)
                    else:
                        # Switching suppliers must preserve the full managed window.
                        marks=','.join('?' for _ in candidate_sources[kind])
                        old_dates=conn.execute(f"""SELECT DISTINCT obs_time::DATE FROM equity_daily_bars
                            WHERE source_id IN ({marks}) AND symbol=? AND obs_time BETWEEN ?::DATE AND ?::DATE""",
                            [*candidate_sources[kind],symbol,candidate_start,request_end]).fetchall()
                        missing={r[0] for r in old_dates}-set(dates.dt.date)
                        if missing:raise ValueError(f'Candidate omitted {len(missing)} existing trading dates')
                        written=store_frame(conn,run,source,symbol,frame,request,adjustment,kind)
                    circuit[source]=0
                    finish_run(conn,run,len(frame),written,dates=(dates.min().date(),dates.max().date()))
                    conn.execute('INSERT OR REPLACE INTO raw.selected_series VALUES (?,?,?,?,?,?,?,?)',
                        [kind,symbol,source,adjustment,candidate_start,request_end,dates.max().date(),run])
                    selected={'symbol':symbol,'source':source,'rows':written,'latest':str(dates.max().date()),'attempts':attempts}
                    break
                except Exception as exc:
                    if not fetched:
                        circuit[source]=circuit.get(source,0)+1
                    finish_run(conn,run,error=str(exc))
                    attempts.append({'source':source,'error':str(exc)})
            if selected:report['success'].append(selected)
            else:report['failed'].append({'symbol':symbol,'attempts':attempts,'error':'all compatible candidates failed'})
            print(f'[sync] {kind} {symbol}: {len(report["success"])} succeeded, {len(report["failed"])} failed',file=sys.stderr,flush=True)
    finally:
        if own:conn.close()
    report['plan']=plan_summary
    return report


def store_index(conn,run,source,factor,frame,request):
    indicator=factor.replace('_','-')
    series=f'{indicator}:{factor}'
    conn.execute('INSERT INTO raw.acquisition_batches(run_id,source_id,symbol,request,records) VALUES (?,?,?,?,?)',
                 [run,source,request['symbol'],json.dumps(request),frame.to_json(orient='records',date_format='iso')])
    data=frame.copy()
    data['date']=pd.to_datetime(data['date'],errors='coerce')
    data['close']=pd.to_numeric(data['close'],errors='coerce')
    if data.empty or data[['date','close']].isna().any().any() or (~data.close.map(lambda v: 0<=v<float('inf'))).any():
        raise ValueError('Invalid index batch; previous data retained')
    if ((data.date < pd.Timestamp(request['start'])) | (data.date > pd.Timestamp(request['end']))).any():
        raise ValueError('Index response outside requested window')
    data=data.drop_duplicates('date',keep='last')
    previous=conn.execute('SELECT DISTINCT obs_time::DATE FROM observations WHERE indicator_id=? AND series_id=? AND obs_time BETWEEN ?::DATE AND ?::DATE',[indicator,series,request['start'],request['end']]).fetchall()
    if {r[0] for r in previous}-set(data.date.dt.date):
        raise ValueError('Index response would lose existing historical dates')
    conn.register('_index_frame',data)
    try:
        conn.execute('BEGIN')
        conn.execute('DELETE FROM observations WHERE source_id=? AND indicator_id=? AND series_id=? AND obs_time BETWEEN ?::DATE AND ?::DATE',
                     [source,indicator,series,request['start'],request['end']])
        conn.execute("INSERT INTO observations(series_id,indicator_id,source_id,obs_time,value,unit,extra) SELECT ?,?,?,date,close,'',?::JSON FROM _index_frame",
                     [series,indicator,source,json.dumps({'run_id':run,'endpoint':'stock_zh_index_daily','unit':'index_points'})])
        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK');raise
    finally:conn.unregister('_index_frame')
    return len(data)


def signature(conn, rule):
    table=rule['table']
    params=list(rule['sources'])
    marks=','.join('?' for _ in params)
    where=f'source_id IN ({marks})'
    if rule.get('indicators'):
        where+=' AND indicator_id IN ('+','.join('?' for _ in rule['indicators'])+')'
        params+=rule['indicators']
    return str(conn.execute(f'SELECT count(*),max(ingested_at),max(obs_time) FROM {table} WHERE {where}',params).fetchone())


def availability(conn, rule, end, symbols=None):
    """Check the requested market scope, or the configured universe by default."""
    if rule.get('logical_kind'):
        kind=rule['logical_kind']
        market_config=settings()['market']
        configured_sources=[candidate['source'] for candidate in market_config['candidates'][kind]]
        if kind=='stock':
            marks=','.join('?' for _ in configured_sources)
            expected={r[0] for r in conn.execute(f"SELECT DISTINCT symbol FROM equity_daily_bars WHERE source_id IN ({marks})",configured_sources).fetchall()}
            expected.update(r[0] for r in conn.execute("SELECT symbol FROM raw.selected_series WHERE kind='stock'").fetchall())
            expected -= excluded_stock_symbols(market_config)
        elif kind=='etf':
            from macro_replay.etf_catalog import ETF_WATCHLIST
            expected={str(item['code']).zfill(6) for item in ETF_WATCHLIST}
            marks=','.join('?' for _ in configured_sources)
            expected.update(r[0] for r in conn.execute(f"SELECT DISTINCT symbol FROM equity_daily_bars WHERE source_id IN ({marks})",configured_sources).fetchall())
        else:expected=set(market_config['indexes'].values())
        selected={r[0]:r[1:] for r in conn.execute('SELECT symbol,source_id,requested_end,latest_date FROM raw.selected_series WHERE kind=?',[kind]).fetchall()}
        if symbols is not None:
            requested={str(symbol).split('.')[0].zfill(6) for symbol in symbols}
            expected &= requested
        if kind=='stock': expected-=set(inactive_symbols(end))
        if kind=='index':
            raw_current=set()
            for factor,symbol in market_config['indexes'].items():
                indicator=factor.replace('_','-')
                series=f'{indicator}:{factor}'
                row=conn.execute("""SELECT max(obs_time)::DATE FROM observations
                    WHERE indicator_id=? AND series_id=?""",[indicator,series]).fetchone()[0]
                if row and str(row)>=end:raw_current.add(symbol)
        else:
            marks=','.join('?' for _ in rule['sources'])
            raw_current={r[0] for r in conn.execute(f"""SELECT symbol FROM equity_daily_bars
                WHERE source_id IN ({marks}) GROUP BY symbol HAVING max(obs_time)::DATE>=?::DATE""",
                [*rule['sources'],end]).fetchall()}
        return [{'key':key,'reason':'no_aligned_candidate','expected':end} for key in sorted(expected)
                if (key not in raw_current and (key not in selected or str(selected[key][1])!=end or str(selected[key][2])!=end))] or ([] if expected else [{'reason':'empty_scope'}])
    table,key=rule['table'],rule['key']
    params=list(rule['sources']); marks=','.join('?' for _ in params)
    where=f'source_id IN ({marks})'
    if rule.get('indicators'):
        where+=' AND indicator_id IN ('+','.join('?' for _ in rule['indicators'])+')'
        params+=rule['indicators']
    rows=conn.execute(f'SELECT {key},max(obs_time)::DATE FROM {table} WHERE {where} GROUP BY 1',params).fetchall()
    if not rows:return [{'reason':'no_data','sources':rule['sources']}]
    gaps=[{'key':key_value,'latest':str(latest),'expected':end} for key_value,latest in rows
          if latest is None or str(latest)<end]
    if rule.get('handler')=='baostock_stock':
        inactive=inactive_symbols(end)
        gaps=[gap for gap in gaps if gap['key'] not in inactive]
    if rule.get('indicators'):
        present={r[0] for r in conn.execute(f'SELECT DISTINCT indicator_id FROM {table} WHERE {where}',params).fetchall()}
        gaps.extend({'key':i,'reason':'no_data'} for i in rule['indicators'] if i not in present)
    return gaps


def run_rule(rule_id,end, symbols=None):
    """Stage 1: one explicitly selected rule, raw writes only, audited readiness."""
    from macro_replay.db import ensure_db
    rule=settings()['rules'][rule_id]
    if not rule['enabled']:raise ValueError(f'Disabled rule: {rule_id}')
    handler=rule['handler']
    conn=ensure_db()
    run=start_run(conn,rule_id,{'end':end,'stage':'acquire'})
    conn.close()
    try:
        if handler=='market':
            outcome=sync_market(end,symbols,kinds=(rule['logical_kind'],))
        elif handler=='baostock_stock':
            from scripts.fetch_baostock_equity_bars import refresh_daily_all
            check=ensure_db()
            try: latest=check.execute("SELECT max(obs_time)::DATE FROM equity_daily_bars WHERE source_id='baostock_history_k_data'").fetchone()[0]
            finally: check.close()
            start=(pd.Timestamp(latest)-pd.Timedelta(days=7)).date().isoformat() if latest else '2021-01-01'
            outcome=refresh_daily_all(start,end,end,refresh_universe=True)
        elif handler=='configured':
            from macro_replay.pipeline import process_indicator
            from macro_replay.config import load_indicators, load_mcp_servers
            from dataclasses import replace
            try: servers=load_mcp_servers()
            except FileNotFoundError: servers={}
            outcome={'success':[], 'failed':[]}
            for indicator in load_indicators():
                if not indicator.series: continue
                try:
                    arguments=dict(indicator.arguments,fetch_end_date=end,end_date=end)
                    process_indicator(replace(indicator,arguments=arguments),servers)
                    outcome['success'].append(indicator.id)
                except Exception as exc: outcome['failed'].append({'indicator':indicator.id,'error':str(exc)})
        elif handler=='index_valuation':
            from scripts.fetch_comein_index_valuation import refresh
            outcome=refresh()
        elif handler=='exchange_margin':
            from .exchange_margin import refresh
            outcome=refresh(end)
        elif handler=='futures':
            check=ensure_db()
            try:
                latest=check.execute("SELECT min(latest) FROM (SELECT indicator_id,max(obs_time)::DATE latest FROM observations WHERE indicator_id IN ('if-close','ic-close','ih-close') GROUP BY 1)").fetchone()[0]
                start=(pd.Timestamp(latest)-pd.Timedelta(days=45)).date().isoformat() if latest else (pd.Timestamp(end)-pd.DateOffset(years=1)).date().isoformat()
            finally: check.close()
            from .isolated import call
            outcome=call('scripts.fetch_cffex_indicators','refresh',(start,end),timeout=180)
            check=ensure_db()
            try: gaps=availability(check,rule,end)
            finally: check.close()
            if gaps:
                fallback=call('scripts.fetch_comein_futures','refresh',kwargs={'limit':500},timeout=240)
                outcome={'attempts':[outcome,fallback]}
        elif handler=='gold':
            from scripts.fetch_london_gold_spot import refresh
            outcome=refresh('2006-08-11',end)
        elif handler=='akshare_options':
            from scripts.fetch_akshare_recovered import refresh
            outcome=refresh('2015-02-09',end)
        elif handler=='etf_shares':
            from scripts.fetch_etf_share_history import refresh
            outcome=refresh('2021-01-01',end)
        else:raise ValueError(f'Unknown handler: {handler}')
    except Exception as exc:
        outcome={'failed':[{'error':str(exc)}]}
    conn=ensure_db()
    try:
        gaps=availability(conn,rule,end,symbols=symbols)
        report={'rule':rule_id,'end':end,'result':outcome,'gaps':gaps,
                'failed':list(outcome.get('failed',[])) if isinstance(outcome,dict) else []}
        if gaps:report['failed'].append({'reason':'dates_not_aligned','count':len(gaps)})
        status='failed' if report['failed'] else 'ready'
        finish_run(conn,run,error=json.dumps(report['failed'],ensure_ascii=False) if report['failed'] else None)
        conn.execute('INSERT OR REPLACE INTO raw.sync_state VALUES (?,?,?,now(),?,?)',
                     [rule_id,end,status,json.dumps(report,ensure_ascii=False,default=str),signature(conn,rule)])
        return report
    finally:conn.close()


def normalize(end):
    """Publish available validated records; stale datasets do not block others."""
    from macro_replay.db import ensure_db
    conn=ensure_db()
    try:
        result=publish(conn)
        result['target_date']=end
        result['stale_series']=conn.execute("""SELECT symbol,price_adjustment,max(trade_date)::VARCHAR
            FROM core.market_daily_bars GROUP BY 1,2 HAVING max(trade_date) < ?::DATE
            ORDER BY symbol,price_adjustment""",[end]).fetchall()
        from .audit import audit_database
        result['audit']=audit_database(conn,end)
        result['all_current']=result['audit']['all_current']
    finally:conn.close()
    return result


def preparation_steps(today,data_end,configured=None):
    """Acquisition steps only. Normalization and derived work follow the gate."""
    return [(f'fetch_{name}',run_rule,(name,data_end)) for name,rule in settings()['rules'].items() if rule['enabled']]


def derived_steps():
    from scripts.compute_margin_derived import compute as margin
    from scripts.compute_index_derived import compute as index
    from scripts.compute_futures_annualized_basis import refresh as basis
    from scripts.compute_baostock_market_breadth import refresh as breadth
    from scripts.compute_ashare_technical_factors import refresh as technical
    return [('compute_margin_derived',margin,()),('compute_baostock_market_breadth',breadth,()),
            ('compute_ashare_technical_factors',technical,()),('compute_index_derived',index,()),
            ('compute_futures_annualized_basis',basis,())]


def main():
    import os
    os.chdir(ROOT)
    parser=argparse.ArgumentParser(description='Stage 1: acquire each source. Stage 2: normalize aligned raw data.')
    parser.add_argument('stage',choices=['run','acquire','normalize','status'],nargs='?',default='run')
    parser.add_argument('--dataset','--source',dest='source',choices=[*settings()['rules'],'all'],default='all')
    parser.add_argument('--end',default=None)
    parser.add_argument('--symbols',help='Optional stock/ETF subset to update and verify')
    args=parser.parse_args()
    args.end=args.end or default_end()
    if args.stage=='run':
        from .workflow import run_pipeline
        result=run_pipeline(args.end,None if args.source=='all' else [args.source],args.symbols.split(',') if args.symbols else None)
    elif args.stage=='normalize':
        from .workflow import writer_lock
        with writer_lock():
            result=normalize(args.end)
            from .snapshot import publish_snapshot
            result['panel_snapshot'] = publish_snapshot(ROOT)
    elif args.stage=='status':
        from macro_replay.db import ensure_db
        conn=ensure_db()
        try:result={'rules':conn.execute('SELECT rule_id,end_date,status,finished_at FROM raw.sync_state ORDER BY rule_id').fetchall()}
        finally:conn.close()
    else:
        from .workflow import writer_lock
        names=[name for name,rule in settings()['rules'].items() if rule['enabled']] if args.source=='all' else [args.source]
        with writer_lock():
            reports=[run_rule(name,args.end,args.symbols.split(',') if args.symbols else None) for name in names]
        result={'reports':reports,'failed':[r['rule'] for r in reports if r['failed']]}
    path=ROOT/'logs'/f'data_sync_{args.stage}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,default=str))
    return 1 if result.get('failed') or result.get('status') in ('partial','failed') else 0
