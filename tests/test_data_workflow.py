from datahub.sync import service
from datahub.sync.workflow import run_pipeline, writer_lock
import json
import pytest


def test_pipeline_continues_after_dataset_failure_and_publishes(monkeypatch,tmp_path):
    calls=[]
    def acquire(name,end,symbols):
        calls.append(name)
        if name=='market_stock':raise ConnectionError('upstream unavailable')
        return {'rule':name,'failed':[]}
    def normalize(end):
        calls.append('normalize')
        return {'status':'success','all_current':False,'stale_series':[['000001','qfq','2026-09-02']]}
    monkeypatch.setattr(service,'run_rule',acquire)
    monkeypatch.setattr(service,'normalize',normalize)
    result=run_pipeline('2026-09-15',['market_stock','market_etf'],root=tmp_path)
    assert calls==['market_stock','market_etf','normalize']
    assert result['status']=='partial'
    assert result['pid'] > 0
    assert result['updated_at']
    assert result['failed']==['market_stock']
    saved=json.loads((tmp_path/'logs/data_sync/latest.json').read_text(encoding='utf-8'))
    assert saved==result
    assert (tmp_path/f"logs/data_sync/{result['run_id']}.json").exists()


def test_pipeline_records_publication_failure(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'run_rule',lambda *args:{'failed':[]})
    def fail(end):raise RuntimeError('database locked')
    monkeypatch.setattr(service,'normalize',fail)
    result=run_pipeline('2026-09-15',['market_etf'],root=tmp_path)
    assert result['status']=='failed'
    assert result['failed']==['normalize']
    assert result['normalization']['error']=='database locked'


def test_pipeline_success_and_lock_release(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'run_rule',lambda *args:{'failed':[]})
    monkeypatch.setattr(service,'normalize',lambda *args:{'status':'success','all_current':True})
    with writer_lock(tmp_path):
        with pytest.raises(RuntimeError,match='Another data workflow'):
            run_pipeline('2026-09-15',['market_etf'],root=tmp_path)
    assert run_pipeline('2026-09-15',['market_etf'],root=tmp_path)['status']=='success'


def test_invalid_dataset_rejected_before_any_work(monkeypatch,tmp_path):
    def forbidden(*args):raise AssertionError('must validate first')
    monkeypatch.setattr(service,'run_rule',forbidden)
    with pytest.raises(ValueError):
        run_pipeline('2026-09-15',['missing'],root=tmp_path)
