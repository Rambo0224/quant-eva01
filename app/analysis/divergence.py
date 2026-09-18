from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd

from .operators import ema


def detect(close, dif, histogram, warmup=35):
    waves = {1: [], -1: []}
    active = {1: None, -1: None}
    current = None
    transitions, states, values = [], [], []
    consecutive = 0
    wave_id = 0

    def transition(signal, state, timestamp, reason):
        signal['state'] = state
        if state == 'formed':
            signal['formed_time'] = timestamp
        if state == 'invalidated':
            signal['invalidated_time'] = timestamp
        transitions.append({**deepcopy(signal), 'date': timestamp, 'reason': reason,
                            'direction': '偏多' if signal['signal_type'] == 'bottom' else '偏空',
                            'attention': state == 'formed'})

    for position, (timestamp, price, diff, bar) in enumerate(zip(close.index, close, dif, histogram)):
        stamp = timestamp.isoformat()
        if not all(np.isfinite(value) for value in (price, diff, bar)):
            for signal in active.values():
                if signal:
                    transition(signal, 'invalidated', stamp, '数据缺口，无法连续验证背离')
            waves, active, current = {1: [], -1: []}, {1: None, -1: None}, None
            consecutive = 0
            values.append(np.nan)
            states.append([])
            continue
        consecutive += 1
        for color, signal in list(active.items()):
            if signal and signal['state'] == 'formed' and color * (diff - signal['compare_dif']) > 0:
                transition(signal, 'invalidated', stamp, 'DIF突破所选比较波段极值，背离失效')
                active[color] = None
        color = 1 if bar > 0 else -1 if bar < 0 else current['color'] if current else 0
        if color and (current is None or color != current['color']):
            if current:
                old_color = current['color']
                waves[old_color].append(deepcopy(current))
                waves[old_color] = waves[old_color][-2:]
                candidate = active[old_color]
                if candidate and candidate['state'] == 'started':
                    transition(candidate, 'invalidated', stamp, '波段结束，候选背离未形成')
                    active[old_color] = None
            wave_id += 1
            current = {'id': wave_id, 'color': color, 'count': 0, 'price': price, 'dif': diff,
                       'price_time': stamp, 'dif_time': stamp, 'used': False}
        if color:
            current['count'] += 1
            for key, value in [('price', price), ('dif', diff)]:
                if color * (value - current[key]) > 0:
                    current[key], current[key + '_time'] = value, stamp
            previous_waves = list(reversed(waves[color]))
            signal = active[color]
            if (consecutive >= warmup and current['count'] >= 2 and previous_waves
                    and signal is None and not current['used']
                    and color * (price - previous_waves[0]['price']) > 0):
                compare = previous_waves[0]
                signal = {'signal_id': f'{color}:{wave_id}', 'signal_type': 'top' if color == 1 else 'bottom',
                          'state': 'started', 'start_time': stamp, 'formed_time': None, 'invalidated_time': None,
                          'price_anchor_time': current['price_time'], 'price_value': float(current['price']),
                          'dif_anchor_time': current['dif_time'], 'dif_value': float(current['dif']),
                          'compare_wave_id': compare['id'], 'compare_price': float(compare['price']),
                          'compare_dif': float(compare['dif']), 'wave_distance': 1}
                active[color] = signal
                transition(signal, 'started', stamp, '价格突破前同色波段极值，等待DIF拐头')
            if signal and signal['state'] == 'started' and position >= 2 and consecutive >= warmup:
                turned = color * (diff - dif.iloc[position - 1]) < 0 and color * (dif.iloc[position - 1] - dif.iloc[position - 2]) >= 0
                if turned:
                    for distance, compare in enumerate(previous_waves, 1):
                        if color * (current['price'] - compare['price']) > 0 and color * (current['dif'] - compare['dif']) < 0:
                            signal.update(price_anchor_time=current['price_time'], price_value=float(current['price']),
                                          dif_anchor_time=current['dif_time'], dif_value=float(current['dif']),
                                          compare_wave_id=compare['id'], compare_price=float(compare['price']),
                                          compare_dif=float(compare['dif']), wave_distance=distance)
                            transition(signal, 'formed', stamp, ('顶' if color == 1 else '底') + '背离形成 · ' + ('邻峰' if distance == 1 else '隔峰'))
                            current['used'] = True
                            break
        snapshot = [deepcopy(signal) for signal in active.values() if signal]
        formed = [signal for signal in snapshot if signal['state'] == 'formed']
        values.append(float(sum(1 if signal['signal_type'] == 'bottom' else -1 for signal in formed)) if consecutive >= warmup else np.nan)
        states.append(snapshot)
    return values, states, transitions


def calculate(frame, params):
    dif = ema(frame.close, params['fast']) - ema(frame.close, params['slow'])
    dea = ema(dif, params['signal'])
    histogram = 2 * (dif - dea)
    values, states, transitions = detect(frame.close, dif, histogram, max(35, params['slow'] + params['signal']))
    result = pd.DataFrame({'value': values, 'diff': dif, 'dea': dea, 'histogram': histogram}, index=frame.index)
    result.attrs.update(states=states, transitions=transitions)
    return result


def enrich(result, calculated, dataset, start, end, history):
    metadata = {'symbol': str(dataset.filters.get('symbol', dataset.id)).split('.')[0],
                'frequency': '1d', 'source_id': dataset.source, 'adjustment_mode': dataset.adjustment}
    snapshot = calculated.attrs['states'][-1] if len(calculated) else []
    result['signals'] = [{**signal, **metadata} for signal in snapshot]
    formed = [signal for signal in snapshot if signal['state'] == 'formed']
    result['events'] = [{**event, **metadata} for event in calculated.attrs['transitions']
                        if pd.Timestamp(start) <= pd.Timestamp(event['date']) < pd.Timestamp(end) + pd.Timedelta(days=1)] if history else []
    if result['status'] not in ('正常', '值得关注'):
        return result
    result['attention'] = bool(formed)
    result['status'] = '值得关注' if formed else '正常'
    result['direction'] = '观察' if len(formed) > 1 else ('偏多' if formed[0]['signal_type'] == 'bottom' else '偏空') if formed else '中性'
    result['reason'] = '；'.join(('底' if signal['signal_type'] == 'bottom' else '顶') + '背离仍有效，形成于 ' + signal['formed_time'][:10] for signal in formed) if formed else '候选背离开始，等待形成' if snapshot else '暂无有效的已形成背离'
    return result
