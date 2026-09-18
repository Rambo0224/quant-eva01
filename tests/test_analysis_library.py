from dataclasses import replace

import duckdb
import pandas as pd
import pytest

from app.analysis.data import Repository, LoadedData
from app.analysis.library import build_library, database_metadata, load_object
from app import quant_server
from test_quant_analysis import sample
from test_quant_api import client


@pytest.fixture
def library_fixture(tmp_path):
    (tmp_path / 'data').mkdir()
    repository = Repository(tmp_path)
    with duckdb.connect(str(repository.path)) as conn:
        conn.execute('CREATE SCHEMA core')
        conn.execute('CREATE TABLE core.instrument_master(instrument_id VARCHAR,symbol VARCHAR,name VARCHAR)')
        conn.execute("INSERT INTO core.instrument_master VALUES ('etf1','510300.SH','沪深300ETF'),('etf2','159919.SZ','沪深300ETF嘉实')")
        conn.execute('''CREATE TABLE core.etf_daily_facts(instrument_id VARCHAR,source_id VARCHAR,
            trade_date DATE,shares DOUBLE,share_change DOUBLE,estimated_flow_amount DOUBLE,
            shares_unit VARCHAR,amount_unit VARCHAR,ingested_at TIMESTAMP)''')
        conn.execute("INSERT INTO core.etf_daily_facts VALUES ('etf1','shares-source','2025-01-03',120,20,80,'份','CNY','2025-01-04')")
    data = sample(length=90)
    first = replace(data.dataset, id='first', name='510300', category='ETF行情', source='price-source', filters={'symbol':'510300'})
    second = replace(first, id='second', filters={'symbol':'510300.SH'}, source='other-source')
    other = replace(first, id='other', filters={'symbol':'159919.SZ'})
    datasets = [first, second, other]
    instruments, facts = database_metadata(repository)
    objects, aliases = build_library(datasets, tmp_path, instruments, facts)
    return repository, datasets, objects, aliases, data


def test_group_by_identity_not_similar_name(library_fixture):
    _, _, objects, aliases, _ = library_fixture
    assert len(objects) == 2
    assert aliases['first'] == aliases['second'] != aliases['other']
    item = next(x for x in objects if x['id'] == aliases['first'])
    assert item['name'] == '沪深300ETF · 510300'
    assert {'close','volume','shares','share_change'} <= set(item['fields'])
    assert item['data_count'] == 3


def test_load_whole_panel_auxiliary_alignment_and_cutoff(library_fixture, monkeypatch):
    repository, datasets, objects, aliases, data = library_fixture
    # The second source is newer only after the cutoff; it must not win by its
    # catalog latest date, nor fill gaps in the selected source retrospectively.
    def load(dataset, end, calendar):
        frame = data.frame.copy()
        if dataset.id == 'second':
            frame = frame.loc[frame.index >= '2025-02-01']
            frame['volume'] = 99999
        return LoadedData(dataset, frame.loc[:end], [])
    monkeypatch.setattr(repository, 'load', load)
    item = next(x for x in objects if x['id'] == aliases['first'])
    loaded, bindings = load_object(repository,item,{d.id:d for d in datasets},'2025-01-31',pd.DatetimeIndex([]),{'close','volume'})
    assert loaded.dataset.source == 'price-source'
    assert loaded.frame.loc['2025-01-03','shares'] == 120
    assert pd.isna(loaded.frame.loc['2025-01-02','shares'])
    assert pd.isna(loaded.frame.loc['2025-01-06','shares'])
    assert loaded.frame.volume.max() < 99999
    assert bindings[-1]['source'] == 'shares-source'


def test_explicit_index_fields_not_etf_or_synthetic_price(tmp_path):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/analysis_objects.yaml').write_text('objects:\n  hs300:\n    name: HS300\n    series: {close: hs300-close, pe: hs300-pe}\n',encoding='utf-8')
    base = sample().dataset
    close = replace(base,id='close',table='observations',filters={'indicator_id':'hs300-close'})
    pe = replace(close,id='pe',filters={'indicator_id':'hs300-pe'})
    objects, aliases = build_library([close,pe],tmp_path)
    assert len(objects) == 1
    assert set(objects[0]['fields']) == {'close','pe'}
    assert {x['field'] for x in objects[0]['members']} == {'close','pe'}


def test_fresh_short_source_does_not_displace_usable_history(library_fixture, monkeypatch):
    repository, datasets, objects, aliases, data = library_fixture
    def load(dataset, end, calendar):
        frame = data.frame.iloc[-5:] if dataset.id == 'second' else data.frame.iloc[:-5]
        return LoadedData(dataset, frame, [])
    monkeypatch.setattr(repository, 'load', load)
    item = next(x for x in objects if x['id'] == aliases['first'])
    loaded, _ = load_object(repository,item,{d.id:d for d in datasets},data.dataset.last_date,
                            pd.DatetimeIndex([]),{'close','volume'},35)
    assert loaded.dataset.id == item['id']
    assert loaded.dataset.source == 'price-source'
    assert len(loaded.frame) == 85


def test_object_ids_work_in_manual_and_radar(client, library_fixture, monkeypatch):
    repository, datasets, objects, aliases, data = library_fixture
    monkeypatch.setattr(quant_server,'repository',repository)
    monkeypatch.setattr(quant_server,'catalog',lambda:datasets)
    monkeypatch.setattr(repository,'load',lambda ds,end,cal: LoadedData(ds,data.frame.loc[:end],[]))
    catalog = client.get('/api/catalog').json()
    assert catalog['dataset_object_ids']['first'] == aliases['first']
    request = {'dataset_ids':[aliases['first']], 'factors':[{'id':'macd_divergence'},{'id':'obv'}],
               'start':'2025-01-01','end':data.dataset.last_date}
    for endpoint in ('/api/analyze','/api/scan'):
        response = client.post(endpoint,json=request)
        assert response.status_code == 200, response.text
        results = response.json()['results']
        assert len(results) == 2
        for result in results:
            assert result['dataset_id'] == aliases['first']
            assert result['status'] in ('正常','值得关注')
            assert any(row['field']=='shares' for row in result['related_data'])
        obv = next(r for r in results if r['factor_id']=='obv')
        assert {r['field'] for r in obv['related_data'] if r['used']} == {'close','volume'}
