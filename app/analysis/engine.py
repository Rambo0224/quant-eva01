from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .data import LoadedData
from .factors import REGISTRY
from .rules import evaluate
from .divergence import enrich


def number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def analyze(loaded: LoadedData, factor_id: str, supplied: dict | None, start: str, end: str, stale_days: int = 7, history: bool = True) -> dict:
    factor = REGISTRY[factor_id]
    params = factor.resolve(supplied)
    lookback = factor.lookback(params)
    frame = loaded.frame.loc[loaded.frame.index < pd.Timestamp(end) + pd.Timedelta(days=1)]
    dataset = loaded.dataset
    result = {'dataset_id': dataset.id, 'dataset_name': dataset.name, 'factor_id': factor.id, 'factor_name': factor.name,
        'params': params, 'source': dataset.source, 'unit': dataset.unit, 'labels': factor.labels, 'status': '数据不足',
        'direction': '—', 'attention': False, 'reason': f'至少需要 {lookback} 期有效数据', 'value': None,
        'as_of': end, 'value_date': None, 'data_date': None, 'stale': False, 'notes': list(loaded.notes), 'history': [], 'events': []}
    result['price_chart'] = {'source': dataset.source, 'adjustment': dataset.adjustment, 'is_price': dataset.price}
    missing_fields = set(factor.required) - set(dataset.fields)
    if factor_id == 'macd_divergence' and dataset.table not in ('equity_daily_bars', 'core.market_daily_bars', 'core.market_daily_bars_by_source'):
        result.update(status='不适用', reason='MACD背离规格适用于股票和ETF独立行情，请选择股票行情或ETF行情')
        return result
    if missing_fields:
        result.update(status='缺少字段', reason='需要字段：' + '、'.join(sorted(missing_fields)))
        return result
    if frame.empty:
        return result
    valid_close = frame.close.dropna()
    if len(valid_close):
        result['data_date'] = valid_close.index[-1].strftime('%Y-%m-%d')
        age = (date.fromisoformat(end) - valid_close.index[-1].date()).days
        result['stale'] = age > stale_days
        if result['stale']:
            result['notes'].append(f'数据距分析截止日 {age} 个自然日，超过 {stale_days} 日新鲜度阈值；以下仅为历史状态。')
    calculated = factor.calculate(frame, params)
    values = calculated.replace([np.inf, -np.inf], np.nan)
    # Keep event snapshots on calculated only: pandas copies attrs on each row
    # selection, which otherwise makes historical serialization quadratic.
    values.attrs = {}
    # Full windows count exchange sessions, not a compressed list of non-missing observations.
    complete = frame[list(factor.required)].notna().all(axis=1)
    window_complete = complete.rolling(lookback).sum().eq(lookback)
    valid = window_complete & values.value.notna()
    # MA uses bias and ATR uses percent for rules; their denominators must also be valid.
    if factor.rule == 'ma':
        valid &= values.bias.notna()
    elif factor.rule == 'atr':
        valid &= values.percent.notna()
    elif factor.rule == 'obv':
        valid &= values.change.notna()
    values = values.where(window_complete)
    visible_positions = np.flatnonzero((frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end)))
    if not len(visible_positions):
        result['reason'] = '所选日期范围内没有数据'
        return result
    events = []
    for pos in visible_positions if history else []:
        timestamp = frame.index[pos].strftime('%Y-%m-%d')
        if history:
            result['history'].append({'date': timestamp, 'input': number(frame.close.iloc[pos]), **{k: number(v) for k, v in values.iloc[pos].items()}})
        if history and valid.iloc[pos]:
            previous = values.iloc[pos - 1] if pos > 0 and valid.iloc[pos - 1] else None
            state = evaluate(factor.rule, values.iloc[pos], previous, params, dataset.price)
            if state['attention']:
                events.append({'date': timestamp, **state})
    result['events'] = events
    if factor_id == 'macd_divergence':
        enrich(result, calculated, dataset, start, end, history)
    pos = int(visible_positions[-1])
    result['value_date'] = frame.index[pos].strftime('%Y-%m-%d')
    if pos + 1 < lookback:
        return result
    if not window_complete.iloc[pos]:
        result.update(status='数据缺失', reason=f'最近 {lookback} 期存在缺失字段或开市日漏数，不生成信号')
        return result
    if not valid.iloc[pos]:
        result.update(status='无法计算', reason='分母为零或计算链存在无效值，不生成信号')
        return result
    previous = values.iloc[pos - 1] if pos > 0 and valid.iloc[pos - 1] else None
    state = evaluate(factor.rule, values.iloc[pos], previous, params, dataset.price)
    result.update(state, value=number(values.value.iloc[pos]), status='值得关注' if state['attention'] else '正常')
    result['components'] = {k: number(v) for k, v in values.iloc[pos].items()}
    if factor_id == 'macd_divergence':
        return enrich(result, calculated, dataset, start, end, history)
    return result
