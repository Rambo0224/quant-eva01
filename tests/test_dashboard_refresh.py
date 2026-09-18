from types import SimpleNamespace
from macro_replay import dashboard_service
from datahub.sync import service


def setup_dashboard(monkeypatch):
    monkeypatch.setattr(dashboard_service,'_write_refresh_report',lambda report:None)
    monkeypatch.setattr(dashboard_service,'load_themes',lambda:[SimpleNamespace(key='test')])
    monkeypatch.setattr(dashboard_service,'load_display_window',lambda:{'start_date':'2020-01-01','end_date':'2026-07-22'})


def test_refresh_all_dashboard_updates_data_before_rerender(monkeypatch):
    setup_dashboard(monkeypatch)
    calls=[]
    def acquire(name,end):
        calls.append('acquire:'+name)
        return {'failed':[]}
    monkeypatch.setattr(service,'run_rule',acquire)
    monkeypatch.setattr(service,'normalize',lambda end:calls.append('normalize'))
    monkeypatch.setattr(service,'derived_steps',lambda:[('compute_test',lambda:calls.append('derive'),())])
    monkeypatch.setattr(dashboard_service,'rerender_theme_from_db',lambda *args:calls.append('render'))
    report=dashboard_service.refresh_all_dashboard('2026-07-22')
    assert report['status']=='success'
    assert calls[-3:]==['derive','normalize','render']
    assert 'acquire:market_stock' in calls
    assert 'acquire:market_index' in calls
    assert 'acquire:market_etf' in calls


def test_failed_acquisition_still_normalizes_available_data(monkeypatch):
    setup_dashboard(monkeypatch)
    monkeypatch.setattr(service,'run_rule',lambda *args:{'failed':['unavailable']})
    def forbidden(*args):raise AssertionError('must not publish partial data')
    calls=[]
    monkeypatch.setattr(service,'normalize',lambda end:calls.append(end))
    monkeypatch.setattr(service,'derived_steps',lambda:[])
    monkeypatch.setattr(dashboard_service,'rerender_theme_from_db',forbidden)
    report=dashboard_service.refresh_all_dashboard('2026-07-22')
    assert report['status']=='failed'
    assert calls==['2026-07-22']
    assert report['steps'][-1]['name']=='normalize_data'
