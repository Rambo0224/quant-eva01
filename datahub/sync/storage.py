from __future__ import annotations

import json
from pathlib import Path
import uuid

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]


def settings():
    return yaml.safe_load((ROOT / 'config/data_sync.yaml').read_text(encoding='utf-8-sig'))


def initialize(conn):
    """Register contracts without scanning history or performing a migration."""
    conn.execute('SET TimeZone=\'UTC\'')
    conn.execute('CREATE SCHEMA IF NOT EXISTS raw; CREATE SCHEMA IF NOT EXISTS core; CREATE SCHEMA IF NOT EXISTS mart')
    conn.execute('''CREATE TABLE IF NOT EXISTS raw.acquisition_batches (
        run_id UUID, source_id VARCHAR, symbol VARCHAR, received_at TIMESTAMPTZ DEFAULT now(),
        request JSON, records JSON, representation VARCHAR DEFAULT 'adapter_frame')''')
    conn.execute('''CREATE TABLE IF NOT EXISTS core.source_contracts (
        source_id VARCHAR PRIMARY KEY, adjustment VARCHAR, volume_multiplier DOUBLE,
        volume_unit VARCHAR, amount_unit VARCHAR, asset_type VARCHAR, priority INTEGER)''')
    conn.execute("""CREATE TABLE IF NOT EXISTS raw.sync_state (
        rule_id VARCHAR PRIMARY KEY,end_date DATE,status VARCHAR,finished_at TIMESTAMPTZ,
        report JSON,raw_signature VARCHAR)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS raw.selected_series (
        kind VARCHAR,symbol VARCHAR,source_id VARCHAR,price_adjustment VARCHAR,
        requested_start DATE,requested_end DATE,latest_date DATE,run_id UUID,
        PRIMARY KEY(kind,symbol))""")
    for sid, spec in settings()['sources'].items():
        conn.execute('''INSERT INTO sources(source_id, source_type, description, meta)
            SELECT ?, 'provider', ?, '{}' WHERE NOT EXISTS(SELECT 1 FROM sources WHERE source_id=?)''',
            [sid, spec['method'], sid])
        conn.execute('''UPDATE sources SET provider=?,endpoint_or_method=?,priority=?,enabled=true,
            license_note='Subject to upstream provider terms',coverage=?,meta=? WHERE source_id=?''',
            [spec['provider'],spec['method'],spec['priority'],spec.get('asset_type','observations'),json.dumps(spec),sid])
        if 'adjustment' in spec:
            conn.execute('''INSERT OR REPLACE INTO core.source_contracts VALUES (?,?,?,?,?,?,?)''',
                         [sid,spec['adjustment'],spec['volume_multiplier'],spec['volume_unit'],spec['amount_unit'],spec['asset_type'],spec['priority']])
    conn.execute("""INSERT INTO sources(source_id,source_type,description,priority,enabled,meta)
        SELECT DISTINCT source_id,'legacy','Contract pending verification',999,true,'{}'::JSON
        FROM (SELECT source_id FROM observations UNION SELECT source_id FROM equity_daily_bars
              UNION SELECT source_id FROM market_daily_bars UNION SELECT source_id FROM etf_share_daily) s
        WHERE source_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM sources x WHERE x.source_id=s.source_id)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS core.data_dictionary(domain VARCHAR,value VARCHAR,description VARCHAR,
        PRIMARY KEY(domain,value))""")
    for domain, value, description in [('frequency','1d','One trading day'),('adjustment','raw','Unadjusted'),
        ('adjustment','qfq','Forward adjusted; refresh complete managed history'),('adjustment','hfq','Backward adjusted'),
        ('volume_unit','share','Stock shares or ETF units'),('amount_unit','CNY','Chinese yuan'),
        ('turnover_rate_unit','percent','One means one percent')]:
        conn.execute('INSERT OR REPLACE INTO core.data_dictionary VALUES (?,?,?)',[domain,value,description])
    for table in ('equity_daily_bars','market_daily_bars','etf_share_daily','observations','raw_payloads'):
        conn.execute(f'CREATE OR REPLACE VIEW raw.{table} AS SELECT * FROM main.{table}')
    conn.execute('CREATE OR REPLACE VIEW mart.charts AS SELECT * FROM main.charts')


def start_run(conn, source, request):
    run = str(uuid.uuid4())
    conn.execute('INSERT INTO ingestion_runs(run_id,source_id,request_range,status) VALUES (?,?,?,\'running\')',
                 [run,source,json.dumps(request)])
    return run


def finish_run(conn, run, read=0, written=0, error=None, dates=None):
    conn.execute('''UPDATE ingestion_runs SET finished_at=current_timestamp,rows_read=?,rows_written=?,
        status=?,error_message=?,date_min=?,date_max=? WHERE run_id=?''',
        [read,written,'failed' if error else 'success',error,*(dates or (None,None)),run])
    if error:
        conn.execute('INSERT INTO ingestion_errors(run_id,stage,error_type,message) VALUES (?,\'acquire_or_store\',\'SyncError\',?)',[run,error])


def store_frame(conn, run, source, symbol, frame, request, adjustment, asset_type='stock'):
    """Atomic source-window replacement. Save adapter output before validation.

    The original vendor response is not reconstructed: representation explicitly
    says adapter_frame. Legacy columns remain in provider units for compatibility.
    """
    conn.execute('INSERT INTO raw.acquisition_batches(run_id,source_id,symbol,request,records) VALUES (?,?,?,?,?)',
                 [run,source,symbol,json.dumps(request),frame.to_json(orient='records',date_format='iso')])
    data = frame.copy()
    data['date'] = pd.to_datetime(data['date'],errors='coerce')
    required = ['open','high','low','close']
    for col in required+['volume','amount','turnover_rate']:
        data[col] = pd.to_numeric(data[col],errors='coerce') if col in data else float('nan')
    bad = data['date'].isna() | data[required].isna().any(axis=1)
    bad |= ~data[required].apply(lambda s: s.map(lambda v: pd.notna(v) and float('-inf') < v < float('inf'))).all(axis=1)
    bad |= (data[required+['volume','amount','turnover_rate']] < 0).any(axis=1)
    for col in ['volume','amount','turnover_rate']:
        bad |= data[col].notna() & ~data[col].map(lambda v: float('-inf') < v < float('inf'))
    bad |= (data.high < data[required].max(axis=1)) | (data.low > data[required].min(axis=1))
    bad |= (data.date < pd.Timestamp(request['start'])) | (data.date > pd.Timestamp(request['end']))
    if bad.any() or data.empty:
        count = int(bad.sum()) or 1
        conn.execute('''INSERT INTO data_quality_results(run_id,table_name,check_name,severity,issue_count,description)
            VALUES (?,'raw.acquisition_batches','bar_validation','error',?,?)''',[run,count,f'{symbol}: invalid or empty batch; prior history retained'])
        raise ValueError(f'{symbol}: {count} invalid rows; source window not replaced')
    duplicates=int(data.duplicated('date',keep='last').sum())
    if duplicates:
        conn.execute("""INSERT INTO data_quality_results(run_id,table_name,check_name,severity,issue_count,description)
            VALUES (?,'raw.acquisition_batches','duplicate_date','warning',?,'Kept last occurrence; full batch retained')""",[run,duplicates])
    data = data.drop_duplicates('date',keep='last')
    if adjustment in ('qfq','hfq'):
        previous = conn.execute("""SELECT DISTINCT obs_time::DATE FROM equity_daily_bars
            WHERE source_id=? AND symbol=? AND obs_time BETWEEN ?::DATE AND ?::DATE""",
            [source,symbol,request['start'],request['end']]).fetchall()
        missing = {r[0] for r in previous} - set(data.date.dt.date)
        if missing:
            conn.execute("""INSERT INTO data_quality_results(run_id,table_name,check_name,severity,issue_count,description)
                VALUES (?,'raw.acquisition_batches','truncated_adjusted_history','error',?,?)""",
                [run,len(missing),f'{symbol}: earlier managed trading dates missing from response'])
            raise ValueError(f'{symbol}: response would remove {len(missing)} historical dates; retained previous history')
    calendar_path=ROOT/'data/baostock_trade_dates.csv'
    if calendar_path.exists():
        calendar=pd.read_csv(calendar_path)
        expected=pd.to_datetime(calendar.loc[calendar.is_trading_day.eq(1),'calendar_date'])
        expected=expected[(expected>=data.date.min()) & (expected<=data.date.max())]
        gaps=set(expected.dt.date)-set(data.date.dt.date)
        if gaps:
            conn.execute("""INSERT INTO data_quality_results(run_id,table_name,check_name,severity,issue_count,description)
                VALUES (?,'raw.acquisition_batches','calendar_gaps','warning',?,?)""",
                [run,len(gaps),f'{symbol}: gaps in locally verified calendar; suspension not inferred or filled'])
    data['symbol'] = symbol
    data['source_id'] = source
    data['extra'] = json.dumps({'price_adjustment':adjustment,'asset_type':asset_type,'run_id':run,
                               'adjustflag':{'qfq':'2','hfq':'1','raw':'3'}[adjustment]})
    conn.register('_sync_frame',data)
    try:
        conn.execute('BEGIN')
        conn.execute('''DELETE FROM main.equity_daily_bars WHERE source_id=? AND symbol=?
            AND obs_time BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)''',[source,symbol,request['start'],request['end']])
        conn.execute('''INSERT INTO main.equity_daily_bars(source_id,symbol,obs_time,open,high,low,close,volume,amount,turnover_rate,extra)
            SELECT source_id,symbol,date,open,high,low,close,volume,amount,turnover_rate,extra::JSON FROM _sync_frame''')
        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK')
        raise
    finally:
        conn.unregister('_sync_frame')
    return len(data)


def publish(conn):
    """Build an atomic core snapshot. This internal primitive never fetches data."""
    initialize(conn)
    run = start_run(conn,'core_normalization',{})
    try:
        conn.execute('BEGIN')
        # Version-1 preview used live views. Remove only those views, never raw tables.
        for table in ('market_daily_bars','market_daily_bars_by_source','observations','observations_by_source',
                      'other_market_daily_bars','other_market_daily_bars_by_source','etf_daily_facts','instrument_identifiers'):
            row=conn.execute("SELECT table_type FROM information_schema.tables WHERE table_schema='core' AND table_name=?",[table]).fetchone()
            if row and row[0]=='VIEW':
                conn.execute(f'DROP VIEW core.{table}')
        previous = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='core' AND table_name='market_daily_bars_by_source'").fetchone()
        if previous:
            conn.execute('CREATE OR REPLACE TEMP TABLE previous_valid_bars AS SELECT * FROM core.market_daily_bars_by_source')
        conn.execute((Path(__file__).with_name('core.sql')).read_text(encoding='utf-8'))
        if previous:
            # Preserve last valid records if a source now contains invalid/missing rows.
            conn.execute("""INSERT INTO core.market_daily_bars_by_source BY NAME
                SELECT p.* FROM previous_valid_bars p WHERE NOT EXISTS (
                SELECT 1 FROM core.market_daily_bars_by_source n WHERE n.instrument_id=p.instrument_id
                AND n.trade_date=p.trade_date AND n.frequency=p.frequency
                AND n.price_adjustment=p.price_adjustment AND n.source_id=p.source_id)""")
            conn.execute("""INSERT INTO core.market_daily_bars BY NAME
                SELECT p.* FROM previous_valid_bars p WHERE NOT EXISTS (
                SELECT 1 FROM core.market_daily_bars n WHERE n.instrument_id=p.instrument_id
                AND n.trade_date=p.trade_date AND n.frequency=p.frequency AND n.price_adjustment=p.price_adjustment)
                QUALIFY row_number() OVER(PARTITION BY instrument_id,trade_date,frequency,price_adjustment ORDER BY ingested_at DESC,source_id)=1""")
            conn.execute('DROP TABLE previous_valid_bars')
        conn.execute("""INSERT INTO data_quality_results(run_id,table_name,instrument_id,check_name,severity,issue_count,description)
            SELECT ?,'raw.equity_daily_bars',instrument_id,quality_issue,'warning',count(*),
            'Excluded from core snapshot; retained in raw'
            FROM core.bar_candidates WHERE quality_issue IS NOT NULL GROUP BY instrument_id,quality_issue""",[run])
        conn.execute("CREATE TABLE IF NOT EXISTS core.publications(run_id UUID,published_at TIMESTAMPTZ,bar_count BIGINT)")
        conn.execute("INSERT INTO core.publications SELECT ?,now(),count(*) FROM core.market_daily_bars",[run])
        conn.execute('COMMIT')
        finish_run(conn,run)
    except Exception as exc:
        conn.execute('ROLLBACK')
        finish_run(conn,run,error=str(exc))
        raise
    return {'status':'success','run_id':run}
