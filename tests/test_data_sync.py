import json
import duckdb
import pandas as pd
import pytest
from macro_replay import db
from datahub.sync import service
from datahub.sync.storage import publish, settings, start_run, store_frame
from datahub.sync.service import sync_market


@pytest.fixture
def conn(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'test.duckdb')
    c=db.ensure_db()
    yield c
    c.close()


def frame(close=10):
    return pd.DataFrame([dict(date='2026-09-01',symbol='000001',open=10,high=12,low=8,close=close,volume=3,amount=3000,turnover_rate=1)])


def test_units_aliases_priority_adjustment_and_snapshot_isolation(conn):
    conn.execute("INSERT INTO equity_daily_bars(source_id,symbol,obs_time,open,high,low,close,volume,extra) VALUES ('baostock_history_k_data','000001.SZ','2026-09-01',10,12,8,10,300,'{\"adjustflag\":\"3\"}')")
    request={'start':'2026-09-01','end':'2026-09-02'}
    run=start_run(conn,'akshare_stock_zh_a_hist_qfq',request)
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(),request,'qfq')
    publish(conn)
    rows=conn.execute('SELECT symbol,volume,price_adjustment FROM core.market_daily_bars ORDER BY price_adjustment').fetchall()
    assert rows==[('000001.SZ',300,'qfq'),('000001.SZ',300,'raw')]
    assert conn.execute('SELECT count(*) FROM core.instrument_master').fetchone()[0]==1
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(11),request,'qfq')
    assert conn.execute("SELECT close FROM core.market_daily_bars WHERE price_adjustment='qfq'").fetchone()[0]==10
    publish(conn)
    assert conn.execute("SELECT close FROM core.market_daily_bars WHERE price_adjustment='qfq'").fetchone()[0]==11
    assert conn.execute('SELECT count(*) FROM core.market_daily_bars').fetchone()[0]==2


def test_core_merges_sources_by_symbol_and_date(conn):
    primary_request={'start':'2026-09-01','end':'2026-09-02'}
    primary=pd.concat([frame(10).assign(date='2026-09-01'),frame(11).assign(date='2026-09-02')],ignore_index=True)
    run_primary=start_run(conn,'akshare_etf_hist_em',primary_request)
    store_frame(conn,run_primary,'akshare_etf_hist_em','512480',primary,primary_request,'raw','etf')
    backup_request={'start':'2026-09-02','end':'2026-09-03'}
    backup=pd.concat([frame(12).assign(date='2026-09-02',high=20),frame(13).assign(date='2026-09-03',high=20)],ignore_index=True)
    run_backup=start_run(conn,'baostock_etf_history_k_data',backup_request)
    store_frame(conn,run_backup,'baostock_etf_history_k_data','512480',backup,backup_request,'raw','etf')
    conn.execute("INSERT INTO raw.selected_series VALUES ('etf','512480','baostock_etf_history_k_data','raw','2026-09-02','2026-09-03','2026-09-03',?)",[run_backup])
    publish(conn)
    rows=conn.execute("""SELECT trade_date,close,source_id FROM core.market_daily_bars
        WHERE symbol='512480.SH' ORDER BY trade_date""").fetchall()
    assert rows == [(pd.Timestamp('2026-09-01').date(),10,'akshare_etf_hist_em'),
                    (pd.Timestamp('2026-09-02').date(),12,'baostock_etf_history_k_data'),
                    (pd.Timestamp('2026-09-03').date(),13,'baostock_etf_history_k_data')]


def test_invalid_batch_retains_previous_history_and_audit(conn):
    request={'start':'2026-09-01','end':'2026-09-02'}
    run=start_run(conn,'akshare_stock_zh_a_hist_qfq',request)
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(),request,'qfq')
    with pytest.raises(ValueError):
        store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(99),request,'qfq')
    assert conn.execute('SELECT close FROM equity_daily_bars').fetchone()[0]==10
    assert conn.execute('SELECT count(*) FROM raw.acquisition_batches').fetchone()[0]==2
    assert conn.execute('SELECT count(*) FROM data_quality_results').fetchone()[0]==1


def test_source_isolation_retry_and_revised_history(conn):
    conn.execute("INSERT INTO equity_daily_bars(source_id,symbol,obs_time) VALUES ('openbb_yfinance','000001','2026-09-15')")
    calls=[]
    def fetcher(kind,symbol,start,end,timeout):
        calls.append((symbol,start,end))
        if len(calls)==1:raise TimeoutError('transient')
        return frame()
    result=sync_market('2026-09-01',['000001'],kinds=('stock',),conn=conn,fetcher=fetcher)
    assert len(calls)==2
    assert calls[-1][1]=='2021-01-04'
    assert len(result['success'])==1
    assert not result['failed']
    assert conn.execute("SELECT status FROM ingestion_runs WHERE source_id='comein_stock_kline'").fetchone()[0]=='success'


def test_unknown_adjustment_is_quarantined(conn):
    conn.execute("INSERT INTO equity_daily_bars(source_id,symbol,obs_time,open,high,low,close,volume) VALUES ('openbb_yfinance','600000.SS','2026-09-01',10,12,8,10,300)")
    publish(conn)
    assert conn.execute('SELECT count(*) FROM core.market_daily_bars').fetchone()[0]==0
    assert conn.execute('SELECT quality_issue FROM core.data_quality_issues').fetchone()[0]=='unknown_adjustment'


def test_normalization_publishes_without_global_readiness_gate(tmp_path,monkeypatch):
    from datahub.sync import service
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'gate.duckdb')
    c=db.ensure_db()
    c.execute('CREATE TABLE core.sentinel AS SELECT 42 AS value')
    c.close()
    report=service.normalize('2026-09-15')
    assert report['status']=='success'
    assert report['target_date']=='2026-09-15'
    with duckdb.connect(str(tmp_path/'gate.duckdb')) as c:
        assert c.execute('SELECT value FROM core.sentinel').fetchone()[0]==42


def test_market_availability_limits_verification_to_requested_symbols(conn):
    run = start_run(conn, 'comein_stock_kline', {})
    for symbol in ('000001', '000002'):
        conn.execute("""INSERT INTO raw.selected_series
            VALUES ('stock', ?, 'comein_stock_kline', 'qfq', '2026-09-17', '2026-09-17', '2026-09-17', ?)""",
                     [symbol, run])
    rule = settings()['rules']['market_stock']
    gaps = service.availability(conn, rule, '2026-09-18', symbols=['000001'])
    assert gaps == [{'key': '000001', 'reason': 'no_aligned_candidate', 'expected': '2026-09-18'}]


def test_fallback_on_stale_primary_and_keep_actual_source(conn):
    calls=[]
    def fetcher(kind,symbol,start,end,timeout):
        calls.append(kind)
        data=frame()
        data['date']='2026-08-31' if kind=='comein_stock_kline' else '2026-09-01'
        return data if kind=='comein_stock_kline' else pd.concat([frame().assign(date='2026-08-31'),data],ignore_index=True)
    result=sync_market('2026-09-01',['000001'],kinds=('stock',),conn=conn,fetcher=fetcher)
    assert calls==['comein_stock_kline','akshare_stock_zh_a_hist_qfq']
    assert result['failed']==[]
    assert result['success'][0]['source']=='akshare_stock_zh_a_hist_qfq'
    assert conn.execute("SELECT source_id FROM equity_daily_bars WHERE obs_time='2026-09-01'").fetchone()[0]=='akshare_stock_zh_a_hist_qfq'
    assert conn.execute('SELECT count(*) FROM ingestion_errors').fetchone()[0]==1
    assert conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='core' AND table_name='market_daily_bars'").fetchone()[0]==0
    publish(conn)
    assert conn.execute("SELECT price_adjustment,volume FROM core.market_daily_bars WHERE trade_date='2026-09-01'").fetchone()==('qfq',300)


def test_first_success_does_not_call_backup(conn):
    calls=[]
    def fetcher(kind,*args):
        calls.append(kind)
        return frame()
    result=sync_market('2026-09-01',['000001'],kinds=('stock',),conn=conn,fetcher=fetcher)
    assert not result['failed']
    assert calls==['comein_stock_kline']


def test_project_excluded_stock_is_never_fetched(conn, monkeypatch):
    config=service.settings()
    config['market']['excluded_symbols']=['000016']
    monkeypatch.setattr(service,'settings',lambda:config)
    result=sync_market('2026-09-01',['000016'],kinds=('stock',),conn=conn,
                       fetcher=lambda *_: (_ for _ in ()).throw(AssertionError('excluded symbol must not fetch')))
    assert result['success']==[]
    assert result['failed']==[]
    assert result['skipped']==[{'symbol':'000016','kind':'stock','reason':'excluded_by_project'}]


def test_incremental_sync_requests_only_the_raw_gap(conn, monkeypatch):
    config=service.settings()
    config['market']['stock_start']='2026-09-01'
    monkeypatch.setattr(service,'settings',lambda:config)
    request={'start':'2026-09-01','end':'2026-09-01'}
    run=start_run(conn,'akshare_stock_zh_a_hist_qfq',request)
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(),request,'qfq')
    calls=[]
    def fetcher(kind,symbol,start,end,timeout):
        calls.append((kind,symbol,start,end))
        return frame().assign(date=end)
    result=sync_market('2026-09-02',['000001'],kinds=('stock',),conn=conn,fetcher=fetcher)
    assert result['failed']==[]
    assert calls==[('comein_stock_kline','000001','2026-09-02','2026-09-02')]


def test_incremental_sync_skips_when_standardized_data_is_current(conn, monkeypatch):
    config=service.settings()
    config['market']['stock_start']='2026-09-01'
    monkeypatch.setattr(service,'settings',lambda:config)
    request={'start':'2026-09-01','end':'2026-09-01'}
    run=start_run(conn,'akshare_stock_zh_a_hist_qfq',request)
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(),request,'qfq')
    publish(conn)
    result=sync_market('2026-09-01',['000001'],kinds=('stock',),conn=conn,
                        fetcher=lambda *_: (_ for _ in ()).throw(AssertionError('network must not run')))
    assert result['failed']==[]
    assert result['skipped']==[{'symbol':'000001','kind':'stock','reason':'standardized_current','standardized_missing_dates':0}]


def test_incremental_sync_uses_existing_raw_input_for_standardized_gap(conn, monkeypatch):
    config=service.settings()
    config['market']['stock_start']='2026-09-01'
    monkeypatch.setattr(service,'settings',lambda:config)
    request={'start':'2026-09-01','end':'2026-09-01'}
    run=start_run(conn,'akshare_stock_zh_a_hist_qfq',request)
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(),request,'qfq')
    result=sync_market('2026-09-01',['000001'],kinds=('stock',),conn=conn,
                        fetcher=lambda *_: (_ for _ in ()).throw(AssertionError('network must not run')))
    assert result['failed']==[]
    assert result['skipped']==[{'symbol':'000001','kind':'stock','reason':'raw_ready_for_standardization','standardized_missing_dates':1}]


def test_incremental_sync_ignores_historical_gap_before_standardized_latest(conn, monkeypatch):
    config=service.settings()
    config['market']['stock_start']='2026-09-01'
    monkeypatch.setattr(service,'settings',lambda:config)
    request={'start':'2026-09-01','end':'2026-09-03'}
    history=pd.concat([frame().assign(date='2026-09-01'),frame().assign(date='2026-09-03')],ignore_index=True)
    run=start_run(conn,'akshare_stock_zh_a_hist_qfq',request)
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',history,request,'qfq')
    publish(conn)
    result=sync_market('2026-09-03',['000001'],kinds=('stock',),conn=conn,
                        fetcher=lambda *_: (_ for _ in ()).throw(AssertionError('historical gap must not fetch')))
    assert result['skipped'][0]['reason']=='standardized_current'


def test_all_candidates_fail_retains_raw_and_core(conn):
    request={'start':'2026-09-01','end':'2026-09-01'}
    run=start_run(conn,'akshare_stock_zh_a_hist_qfq',request)
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(),request,'qfq')
    publish(conn)
    def fetcher(*args):return frame(99)
    result=sync_market('2026-09-02',['000001'],kinds=('stock',),conn=conn,fetcher=fetcher)
    assert len(result['failed'])==1
    assert len(result['failed'][0]['attempts'])==len(settings()['market']['candidates']['stock'])
    assert conn.execute('SELECT close FROM core.market_daily_bars').fetchone()[0]==10
    assert conn.execute('SELECT close FROM equity_daily_bars').fetchone()[0]==10


def test_ready_backup_allows_normalization_without_primary_ready(tmp_path,monkeypatch):
    from datahub.sync import service
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'ready.duckdb')
    config=service.settings()
    config['rules']={'market_stock':config['rules']['market_stock']}
    monkeypatch.setattr(service,'settings',lambda:config)
    c=db.ensure_db()
    def fetcher(kind,*args):
        if kind=='comein_stock_kline':raise ConnectionError('primary offline')
        return frame()
    result=sync_market('2026-09-01',['000001'],kinds=('stock',),conn=c,fetcher=fetcher)
    assert not result['failed']
    assert service.availability(c,config['rules']['market_stock'],'2026-09-01')==[]
    c.execute("INSERT INTO raw.sync_state VALUES ('market_stock','2026-09-01','ready',now(),'{}',?)",[service.signature(c,config['rules']['market_stock'])])
    c.close()
    assert service.normalize('2026-09-01')['status']=='success'
    with duckdb.connect(str(tmp_path/'ready.duckdb')) as c:
        assert c.execute('SELECT source_id FROM core.market_daily_bars').fetchone()[0]=='akshare_stock_zh_a_hist_qfq'


def test_declared_adjustment_mismatch_tries_backup(conn):
    calls=[]
    def fetcher(kind,*args):
        calls.append(kind)
        data=frame()
        data['price_adjustment']='raw' if kind=='comein_stock_kline' else '2'
        return data
    result=sync_market('2026-09-01',['000001'],kinds=('stock',),conn=conn,fetcher=fetcher)
    assert calls==['comein_stock_kline','akshare_stock_zh_a_hist_qfq']
    assert result['success'][0]['source']=='akshare_stock_zh_a_hist_qfq'


def test_truncated_history_cannot_replace_source_window(conn):
    request={'start':'2026-08-31','end':'2026-09-01'}
    run=start_run(conn,'akshare_stock_zh_a_hist_qfq',request)
    history=pd.concat([frame().assign(date='2026-08-31'),frame()],ignore_index=True)
    store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',history,request,'qfq')
    with pytest.raises(ValueError,match='historical dates'):
        store_frame(conn,run,'akshare_stock_zh_a_hist_qfq','000001',frame(),request,'qfq')
    assert conn.execute('SELECT count(*) FROM equity_daily_bars').fetchone()[0]==2
