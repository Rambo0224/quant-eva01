"""Calculation conventions and point-in-time behavior, independent of live providers."""
from dataclasses import replace

import duckdb
import numpy as np
import pandas as pd
import pytest

from app.analysis.data import Dataset, LoadedData, Repository
from app.analysis.engine import analyze
from app.analysis.factors import REGISTRY
from app.analysis.operators import sma


def sample(values=None, length=180):
    dates = pd.bdate_range('2025-01-01', periods=length if values is None else len(values))
    close = np.asarray(values if values is not None else 100 + np.arange(length) * .05 + np.sin(np.arange(length)), dtype=float)
    frame = pd.DataFrame({'close': close, 'open': close-.1, 'high': close+1, 'low': close-1,
        'volume': 1000 + 30 * np.cos(np.arange(len(close)))}, index=dates)
    ds = Dataset('test', '测试价格', '股票行情', 'fixture', '元', list(frame.columns), len(frame),
        str(dates[0].date()), str(dates[-1].date()), True, 'CN', '前复权', 'equity_daily_bars', {})
    return LoadedData(ds, frame, [])


def calculate(loaded, factor, params=None, **kwargs):
    return analyze(loaded, factor, params, loaded.dataset.first_date, loaded.dataset.last_date, **kwargs)


def test_recursive_sma_resumes_from_last_valid_state():
    actual = sma(pd.Series([3., 6., np.nan, 9.]), 3)
    assert actual.iloc[1] == pytest.approx(4)
    assert pd.isna(actual.iloc[2])
    assert actual.iloc[3] == pytest.approx(17/3)


def test_boll_uses_population_standard_deviation():
    data = sample([1, 2, 3, 4])
    result = calculate(data, 'boll', {'n': 4})
    assert result['components']['upper'] == pytest.approx(2.5 + 2*np.sqrt(1.25))


@pytest.mark.parametrize('factor', ['rsi', 'boll', 'zscore', 'kdj', 'vr'])
def test_zero_denominator_never_generates_signal(factor):
    data = sample(np.full(180, 100.))
    if factor == 'kdj':
        data.frame['high'] = data.frame['low'] = 100.
    result = calculate(data, factor)
    assert result['status'] == '无法计算'
    assert not result['attention']
    assert result['direction'] == '—'
    assert result['value'] is None


def test_constant_series_is_not_extreme_percentile():
    result = calculate(sample(np.full(180, 100.)), 'percentile')
    assert result['value'] == 50
    assert not result['attention']


def test_volume_baseline_excludes_current_day():
    data = sample(length=6)
    data.frame['volume'] = [10, 10, 10, 10, 10, 100]
    result = calculate(data, 'volume_ratio', {'n': 5})
    assert result['value'] == 10
    assert result['attention']


def test_zscore_baseline_excludes_current_day():
    result = calculate(sample([1,2,3,20]), 'zscore', {'n': 3})
    assert result['value'] == pytest.approx(18 / np.std([1,2,3], ddof=0))


def test_insufficient_data_and_missing_window_are_distinct():
    assert calculate(sample(length=10), 'ma')['status'] == '数据不足'
    data = sample()
    data.frame.iloc[-2, data.frame.columns.get_loc('close')] = np.nan
    assert calculate(data, 'ma')['status'] == '数据缺失'


def test_close_only_dataset_cannot_calculate_atr():
    data = sample()
    data.dataset = replace(data.dataset, fields=['close'])
    assert calculate(data, 'atr')['status'] == '缺少字段'


@pytest.mark.parametrize('factor', list(REGISTRY))
def test_factor_history_does_not_change_when_future_data_changes(factor):
    data = sample()
    before = calculate(data, factor)
    cutoff = 140
    for col in data.frame:
        data.frame.iloc[cutoff:, data.frame.columns.get_loc(col)] *= 7
    after = calculate(data, factor)
    assert before['history'][:cutoff] == after['history'][:cutoff]


def test_stale_result_is_explicitly_historical():
    data = sample()
    end = (data.frame.index[-1] + pd.Timedelta(days=15)).strftime('%Y-%m-%d')
    result = analyze(data, 'ma', None, data.dataset.first_date, end)
    assert result['stale']
    assert result['value_date'] == data.dataset.last_date
    assert any('历史状态' in note for note in result['notes'])


def test_repository_preserves_confirmed_suspension_and_unconfirmed_gaps(tmp_path):
    (tmp_path/'data').mkdir()
    (tmp_path/'data/baostock_trade_dates.csv').write_text(
        'calendar_date,is_trading_day\n2025-01-02,1\n2025-01-03,1\n2025-01-04,0\n2025-01-05,0\n2025-01-06,1\n2025-01-07,1\n2025-01-08,1\n', encoding='utf-8')
    with duckdb.connect(str(tmp_path/'data/replay.duckdb')) as conn:
        conn.execute('CREATE TABLE equity_daily_bars(symbol TEXT,source_id TEXT,obs_time TIMESTAMP,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,extra JSON,ingested_at TIMESTAMP)')
        conn.execute("INSERT INTO equity_daily_bars VALUES ('A','test','2025-01-02',10,11,9,10,100,'{}',now()), ('A','test','2025-01-03',NULL,NULL,NULL,NULL,NULL,'{\"tradestatus\":\"0\"}',now()), ('A','test','2025-01-07',12,13,11,12,120,'{}',now())")
    repo = Repository(tmp_path)
    ds = repo.catalog()[0]
    loaded = repo.load(ds, '2025-01-08')
    assert list(loaded.frame.index.strftime('%Y-%m-%d')) == ['2025-01-02','2025-01-03','2025-01-06','2025-01-07']
    assert loaded.frame.loc['2025-01-03', 'close'] == 10
    assert loaded.frame.loc['2025-01-03', 'volume'] == 0
    assert pd.isna(loaded.frame.loc['2025-01-06','close'])
    assert repo.load(ds, '2025-01-03').frame.index.max() == pd.Timestamp('2025-01-03')


def test_invalid_parameter_combinations_rejected():
    with pytest.raises(ValueError):
        REGISTRY['macd'].resolve({'fast':30,'slow':20})
    with pytest.raises(ValueError):
        REGISTRY['rsi'].resolve({'lower':80,'upper':20})
    with pytest.raises(ValueError):
        REGISTRY['ma'].resolve({'n':2.5})
