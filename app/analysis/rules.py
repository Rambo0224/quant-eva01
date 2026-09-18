"""Rules consume valid factor values; statistical extremes aren't trade directions."""
from __future__ import annotations

import pandas as pd


def evaluate(rule: str, row: pd.Series, previous: pd.Series | None, params: dict, price: bool) -> dict:
    value = float(row['value'])
    direction, reason, attention = '中性', '未触发关注阈值', False
    if rule == 'ma':
        bias = row['bias']
        direction = ('偏多' if bias > 0 else '偏空' if bias < 0 else '中性') if price else '观察'
        reason = '位于均线上方' if bias > 0 else '位于均线下方' if bias < 0 else '位于均线上'
        if previous is not None and pd.notna(previous['bias']):
            if bias > 0 >= previous['bias']:
                attention, reason = True, '向上穿越均线'
            elif bias < 0 <= previous['bias']:
                attention, reason = True, '向下穿越均线'
    elif rule == 'macd':
        direction = ('偏多' if value > 0 else '偏空' if value < 0 else '中性') if price else '观察'
        reason = 'DIFF高于DEA' if value > 0 else 'DIFF低于DEA' if value < 0 else 'DIFF等于DEA'
        if previous is not None:
            old = previous['value']
            if value > 0 >= old:
                attention, reason = True, 'MACD柱向上过零'
            elif value < 0 <= old:
                attention, reason = True, 'MACD柱向下过零'
    elif rule in ('rsi', 'percentile', 'kdj', 'vr'):
        attention = value >= params['upper'] or value <= params['lower']
        direction = '观察' if attention else '中性'
        if value >= params['upper']:
            reason = f"进入高位区间（≥{params['upper']:g}），关注延续或回落"
        elif value <= params['lower']:
            reason = f"进入低位区间（≤{params['lower']:g}），关注企稳或继续走弱"
    elif rule == 'boll':
        attention = abs(value) > 1
        direction = '观察' if attention else '中性'
        reason = '突破布林上轨' if value > 1 else '跌破布林下轨' if value < -1 else '位于布林带内'
    elif rule in ('zscore', 'roc'):
        attention = abs(value) >= params['threshold']
        if rule == 'roc' and price:
            direction = '偏多' if value > 0 else '偏空' if value < 0 else '中性'
        else:
            direction = '观察' if attention else '中性'
        reason = f"{'向上' if value > 0 else '向下'}偏离达到关注阈值" if attention else '未触发关注阈值'
    elif rule in ('atr', 'volatility', 'volume_ratio'):
        metric = row['percent'] if rule == 'atr' else value
        attention = bool(metric >= params['threshold'])
        direction = '观察' if attention else '中性'
        reason = ('成交量显著放大' if rule == 'volume_ratio' else '波动超过关注阈值') if attention else '未触发关注阈值'
    elif rule == 'obv':
        change = row['change']
        direction = ('偏多' if change > 0 else '偏空' if change < 0 else '中性') if price else '观察'
        reason = '区间内上涨日量能占优' if change > 0 else '区间内下跌日量能占优' if change < 0 else '区间量能平衡'
    return {'direction': direction, 'reason': reason, 'attention': bool(attention)}
