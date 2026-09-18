from types import SimpleNamespace
import json
import duckdb
import pandas as pd
import pytest

from macro_replay import db
from datahub.sync import service, audit
from datahub.sync.storage import publish


@pytest.fixture
def conn(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA_DIR',tmp_path)
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'coverage.duckdb')
    monkeypatch.setattr(audit,'ROOT',tmp_path)
    c=db.ensure_db()
    yield c
    c.close()


def test_one_supplier_suffices_but_missing_indicator_does_not(conn):
    conn.execute("INSERT INTO observations(indicator_id,series_id,source_id,obs_time,value) VALUES ('a','a:value','backup','2026-09-16',1)")
    rule=dict(table='observations',key='series_id',sources=['primary','backup'],indicators=['a','b'])
    gaps=service.availability(conn,rule,'2026-09-16')
    assert gaps==[{'key':'b','reason':'no_data'}]
    rule['indicators']=['a']
    assert service.availability(conn,rule,'2026-09-16')==[]


def test_audit_finds_absent_configured_and_unregistered_stored_indicators(conn,monkeypatch):
    from macro_replay import config
    monkeypatch.setattr(config,'load_indicators',lambda:[SimpleNamespace(id='missing',source='catalog',series=[])])
    conn.execute("INSERT INTO observations(indicator_id,series_id,source_id,obs_time,value) VALUES ('forgotten','forgotten:x','old','2026-09-16',1)")
    publish(conn)
    report=audit.audit_database(conn,'2026-09-16')
    issues={r['key']:r for r in report['issues']}
    assert issues['missing']['status']=='unregistered'
    assert issues['forgotten']['status']=='unregistered'
    assert not report['all_current']


def test_etf_day_not_complete_when_one_symbol_is_missing(conn):
    from scripts.fetch_etf_share_history import _existing_dates
    conn.execute("INSERT INTO etf_share_daily(source_id,symbol,obs_time,shares) VALUES ('s','510300','2026-09-15',1),('s','512480','2026-09-15',1),('s','510300','2026-09-16',1)")
    assert _existing_dates(conn,'s',['510300','512480'])=={pd.Timestamp('2026-09-15')}
    assert _existing_dates(conn,'s',['510300','159995'])==set()


def test_full_workflow_derives_after_partial_acquisition_before_publication(monkeypatch,tmp_path):
    from datahub.sync import workflow
    calls=[]
    monkeypatch.setattr(workflow,'settings',lambda:{'rules':{'test':{'enabled':True}}})
    monkeypatch.setattr(service,'run_rule',lambda *args:{'failed':['offline']})
    monkeypatch.setattr(service,'derived_steps',lambda:[('derive',lambda:calls.append('derive'),())])
    monkeypatch.setattr(service,'normalize',lambda end:calls.append('publish') or {'all_current':False})
    report=workflow.run_pipeline('2026-09-16',root=tmp_path)
    assert calls==['derive','publish']
    assert report['status']=='partial'


def test_default_end_uses_completed_session_and_holidays(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'ROOT',tmp_path)
    (tmp_path/'data').mkdir()
    pd.DataFrame({'calendar_date':['2026-09-15','2026-09-16','2026-09-17'],
                  'is_trading_day':[1,1,0]}).to_csv(tmp_path/'data/baostock_trade_dates.csv',index=False)
    assert service.default_end('2026-09-16 14:00')=='2026-09-15'
    assert service.default_end('2026-09-16 15:00')=='2026-09-16'
    assert service.default_end('2026-09-16 19:00')=='2026-09-16'
    assert service.default_end('2026-09-17 19:00')=='2026-09-16'


def test_default_end_falls_back_to_akshare_calendar(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'ROOT',tmp_path)
    (tmp_path/'data').mkdir()
    pd.DataFrame({'calendar_date':['2026-09-15'],'is_trading_day':[1]}).to_csv(tmp_path/'data/baostock_trade_dates.csv',index=False)
    monkeypatch.setattr(service,'fetch_worker',lambda *args: (_ for _ in ()).throw(TimeoutError('BaoStock unavailable')))
    monkeypatch.setattr('datahub.adapters.akshare_public.fetch_trade_dates',
                        lambda *args:pd.DatetimeIndex(['2026-09-16','2026-09-17']))
    assert service.default_end('2026-09-17 16:00')=='2026-09-17'


def test_market_day_checkpoint_is_not_a_date_only_flag(conn):
    from scripts.fetch_baostock_equity_bars import _verified_complete_day
    conn.execute('CREATE TABLE raw.daily_market_manifest(source_id VARCHAR,trade_date DATE,symbols JSON)')
    conn.execute("INSERT INTO raw.daily_market_manifest VALUES ('s','2026-09-16','[\"000001\",\"600000\"]')")
    conn.execute("INSERT INTO equity_daily_bars(source_id,symbol,obs_time) VALUES ('s','000001','2026-09-16')")
    assert not _verified_complete_day(conn,'s','2026-09-16')
    conn.execute("INSERT INTO equity_daily_bars(source_id,symbol,obs_time) VALUES ('s','600000','2026-09-16')")
    assert _verified_complete_day(conn,'s','2026-09-16')
    assert not _verified_complete_day(conn,'s','2026-09-15')
    conn.execute("UPDATE raw.daily_market_manifest SET symbols='[]'")
    assert not _verified_complete_day(conn,'s','2026-09-16')


def test_margin_fallback_checks_units_before_summing():
    from datahub.sync.exchange_margin import combine_day
    sh={'rzye':200_000_000,'rzmre':100_000_000,'rqylje':50_000_000}
    sz={'metadata':{'cols':{'jrrzye':'融资余额<br>(亿元)','jrrzmr':'融资买入额<br>(亿元)','jrrjye':'融券余额<br>(亿元)'}},
        'data':[{'jrrzye':'3.00','jrrzmr':'2.00','jrrjye':'1.50'}]}
    assert combine_day(sh,sz)=={'margin-balance':5,'margin-purchase':3,'short-balance':2}
    sz['metadata']['cols']['jrrzye']='融资余额'
    with pytest.raises(ValueError,match='unit'): combine_day(sh,sz)


def test_ifind_quota_answer_is_an_error_not_an_empty_success(monkeypatch):
    from macro_replay.sources import ifind
    response={'ok':True,'data':{'result':{'content':[{'type':'text','text':json.dumps({'data':{'answer':'用户使用工具已超限'}})}]}}}
    monkeypatch.setattr(ifind,'_load_ifind_skill_call_module',lambda:SimpleNamespace(call=lambda *a:response))
    with pytest.raises(RuntimeError,match='超限'):
        ifind._fetch_edb_direct_dataframe('query','value','value')


def test_edb_backup_preserves_gateway_identity_and_exact_indicator(monkeypatch):
    from macro_replay.sources import ifind
    from datahub.adapters import comein_mcp
    monkeypatch.setattr(ifind,'_fetch_edb_direct_dataframe',lambda *args:(pd.DataFrame(),None))
    markdown='| 日期 | 指定指标（单位：元） |\n|---|---|\n|2026-09-16|123|'
    class Gateway:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def call_tool(self,name,arguments):
            assert name=='get_economic_indicator_data'
            assert arguments['index_name']=='指定指标'
            return {'content':[{'type':'text','text':json.dumps({'data':{'answer':markdown}})}]}
    monkeypatch.setattr(comein_mcp,'ComeinMcpClient',Gateway)
    frame,unit=ifind.fetch_edb_direct_dataframe('2026-09-01至2026-09-16','value','指定指标',comein_index_name='指定指标')
    assert unit=='元' and frame.iloc[0]['value']==123
    assert frame.attrs['source_id']=='comein_ifind_edb'
    with pytest.raises(RuntimeError,match='different indicator'):
        ifind.fetch_edb_direct_dataframe('2026-09-01至2026-09-16','value','其他指标',comein_index_name='指定指标')
