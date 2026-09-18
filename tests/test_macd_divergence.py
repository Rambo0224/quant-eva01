from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from app.analysis.divergence import detect
from app.analysis.engine import analyze
from app.analysis.factors import REGISTRY
from test_quant_analysis import sample


def annotated_case(kind, variant):
    if variant == 'skip':
        prices = [8,7,8,10,10,10,9,10,11,11,8,6,7,6]
        dif = [-6,-8,-7,0,1,-1,-2,-1,0,1,-3,-4,-3,-9]
        bars = [-1,-1,-1,1,1,-1,-1,-1,1,1,-1,-1,-1,-1]
        start, formed, invalidated, distance = 46,47,48,2
    else:
        prices = [10,9,10,11,11,9,8,8,9,7]
        dif = [-3,-5,-4,0,1,-2,-3,-3 if variant == 'plateau' else -2.5,-2,-6]
        bars = [-1,-1,-1,1,1,-1,-1,-1,-1,-1]
        start, formed, invalidated, distance = 41,43 if variant == 'plateau' else 42,44,1
    if kind == 'top':
        prices = [30-value for value in prices]
        dif = [-value for value in dif]
        bars = [-value for value in bars]
    index = pd.date_range('2025-01-01', periods=35+len(prices))
    close = pd.Series([12.]*35+prices,index=index)
    diff = pd.Series([0.]*35+dif,index=index)
    hist = pd.Series([0.]*35+bars,index=index)
    return close, diff, hist, (start, formed, invalidated, distance)


@pytest.mark.parametrize('kind', ['top','bottom'])
@pytest.mark.parametrize('variant', ['neighbor','plateau','skip'])
def test_six_manually_annotated_state_machine_cases(kind, variant):
    close, dif, hist, (start, formed, invalidated, distance) = annotated_case(kind,variant)
    values, states, transitions = detect(close,dif,hist)
    target = [event for event in transitions if event['signal_type']==kind]
    assert [event['state'] for event in target] == ['started','formed','invalidated']
    assert [event['date'] for event in target] == [close.index[position].isoformat() for position in (start,formed,invalidated)]
    assert target[1]['wave_distance'] == distance
    assert target[1]['formed_time'] == close.index[formed].isoformat()
    assert target[1]['invalidated_time'] is None
    assert target[2]['invalidated_time'] == close.index[invalidated].isoformat()
    assert not states[-1]
    assert values[formed] == (1 if kind=='bottom' else -1)
    assert values[-1] == 0
    if variant=='plateau':
        assert target[1]['dif_anchor_time'] == close.index[start].isoformat()
        assert target[1]['price_anchor_time'] == close.index[start].isoformat()


def test_every_prefix_preserves_all_prior_transitions():
    close,dif,hist,_ = annotated_case('bottom','skip')
    full = detect(close,dif,hist)
    for length in range(1,len(close)+1):
        partial = detect(close.iloc[:length],dif.iloc[:length],hist.iloc[:length])
        assert partial[1] == full[1][:length]
        assert partial[2] == [event for event in full[2] if event['date']<=close.index[length-1].isoformat()]


def test_gap_invalidates_and_does_not_bridge_waves():
    close,dif,hist,(_,formed,_,_) = annotated_case('bottom','neighbor')
    close.iloc[formed+1] = np.nan
    _,states,events = detect(close,dif,hist)
    assert events[-1]['reason'] == '数据缺口，无法连续验证背离'
    assert not states[-1]


def test_zero_bars_keep_previous_wave_and_initial_zeros_do_not_look_ahead():
    close,dif,hist,_ = annotated_case('bottom','plateau')
    baseline = detect(close,dif,hist)
    hist.iloc[42] = 0
    actual = detect(close,dif,hist)
    assert actual[2] == baseline[2]
    assert all(not state for state in actual[1][:35])


def test_engine_excludes_future_and_non_price_inputs():
    data=sample(length=180)
    end=data.frame.index[120].strftime('%Y-%m-%d')
    first=analyze(data,'macd_divergence',None,data.dataset.first_date,end)
    data.frame.iloc[121:,data.frame.columns.get_loc('close')]*=10
    second=analyze(data,'macd_divergence',None,data.dataset.first_date,end)
    assert first==second
    data.dataset=replace(data.dataset, table='observations', price=False)
    assert analyze(data,'macd_divergence',None,data.dataset.first_date,end)['status']=='不适用'


def test_factor_warmup_and_full_metadata():
    data=sample(length=34)
    assert analyze(data,'macd_divergence',None,data.dataset.first_date,data.dataset.last_date)['status']=='数据不足'
    data=sample(length=500)
    result=analyze(data,'macd_divergence',None,data.dataset.first_date,data.dataset.last_date)
    assert result['status'] in ('正常','值得关注')
    assert 'signals' in result
    assert all(event['frequency']=='1d' and event['source_id']=='fixture' for event in result['events'])
    assert REGISTRY['macd_divergence'].lookback(REGISTRY['macd_divergence'].resolve())==35
