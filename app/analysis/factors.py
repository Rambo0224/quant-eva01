from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .operators import divide, ema, percentile, sma
from .divergence import calculate as macd_divergence


@dataclass(frozen=True)
class Parameter:
    label: str
    default: float
    minimum: float
    maximum: float
    step: float = 1


@dataclass(frozen=True)
class Factor:
    id: str
    name: str
    category: str
    description: str
    required: tuple[str, ...]
    parameters: dict[str, Parameter]
    lookback: Callable[[dict], int]
    calculate: Callable[[pd.DataFrame, dict], pd.DataFrame]
    rule: str
    labels: dict[str, str] = field(default_factory=dict)

    def resolve(self, supplied: dict | None = None) -> dict:
        supplied = supplied or {}
        if set(supplied) - self.parameters.keys():
            raise ValueError(f"{self.name} 存在未知参数")
        result = {}
        for key, spec in self.parameters.items():
            value = float(supplied.get(key, spec.default))
            if not np.isfinite(value) or not spec.minimum <= value <= spec.maximum:
                raise ValueError(f"{self.name}：{spec.label} 应在 {spec.minimum} 至 {spec.maximum} 之间")
            if spec.step == 1 and not value.is_integer():
                raise ValueError(f"{self.name}：{spec.label} 必须为整数")
            result[key] = int(value) if spec.step == 1 else value
        if 'fast' in result and result['fast'] >= result['slow']:
            raise ValueError('MACD 快周期必须小于慢周期')
        if 'lower' in result and result['lower'] >= result['upper']:
            raise ValueError(f'{self.name} 下阈值必须小于上阈值')
        return result


REGISTRY: dict[str, Factor] = {}


def register(factor: Factor) -> None:
    if factor.id in REGISTRY:
        raise ValueError(f'Duplicate factor: {factor.id}')
    REGISTRY[factor.id] = factor


def output(value: pd.Series, **columns) -> pd.DataFrame:
    return pd.DataFrame({'value': value, **columns})


def moving_average(d, p):
    value = d.close.rolling(p['n']).mean()
    return output(value, reference=d.close, bias=100 * divide(d.close - value, value.abs()))


def exponential_average(d, p):
    value = ema(d.close, p['n'])
    return output(value, reference=d.close, bias=100 * divide(d.close - value, value.abs()))


def macd(d, p):
    diff = ema(d.close, p['fast']) - ema(d.close, p['slow'])
    dea = ema(diff, p['signal'])
    return output(2 * (diff - dea), diff=diff, dea=dea)


def rsi(d, p):
    change = d.close.diff()
    return output(100 * divide(sma(change.clip(lower=0), p['n']), sma(change.abs(), p['n'])))


def roc(d, p):
    previous = d.close.shift(p['n'])
    return output(100 * divide(d.close - previous, previous.abs()))


def boll(d, p):
    mid = d.close.rolling(p['n']).mean()
    std = d.close.rolling(p['n']).std(ddof=0)
    width = p['k'] * std
    return output(divide(d.close - mid, width), mid=mid, upper=mid + width, lower=mid - width, reference=d.close)


def zscore(d, p):
    baseline = d.close.shift(1).rolling(p['n'])
    return output(divide(d.close - baseline.mean(), baseline.std(ddof=0)))


def atr(d, p):
    previous = d.close.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - previous).abs(), (d.low - previous).abs()], axis=1).max(axis=1)
    tr = tr.where(previous.notna() & d[['high', 'low', 'close']].notna().all(axis=1))
    value = sma(tr, p['n'])
    return output(value, percent=100 * divide(value, d.close.abs()))


def kdj(d, p):
    low, high = d.low.rolling(p['n']).min(), d.high.rolling(p['n']).max()
    rsv = 100 * divide(d.close - low, high - low)
    k = sma(rsv, 3)
    dd = sma(k, 3)
    return output(k, d=dd, j=3 * k - 2 * dd)


def volume_ratio(d, p):
    return output(divide(d.volume, d.volume.shift(1).rolling(p['n']).mean()))


def obv(d, p):
    change = np.sign(d.close.diff()) * d.volume
    if len(change) and pd.notna(d.close.iloc[0]) and pd.notna(d.volume.iloc[0]):
        change.iloc[0] = 0
    value = change.cumsum()
    # Once a gap exists the absolute cumulative value is unknowable.
    value = value.where(change.notna().cummin())
    return output(value, change=value.diff(p['n']))


def vr(d, p):
    delta = d.close.diff()
    up = d.volume.where(delta > 0, 0).where(delta.notna())
    down = d.volume.where(delta < 0, 0).where(delta.notna())
    return output(100 * divide(up.rolling(p['n']).sum(), down.rolling(p['n']).sum()))


def realized_volatility(d, p):
    returns = divide(d.close, d.close.shift(1)) - 1
    return output(returns.rolling(p['n']).std(ddof=0) * np.sqrt(p['annual']) * 100)


N = lambda default=20: Parameter('计算周期', default, 2, 500)
THRESHOLD = lambda label, value: Parameter(label, value, 0.1, 100, 0.1)
WINDOW = lambda p: p['n']
NEXT_WINDOW = lambda p: p['n'] + 1

register(Factor('macd_divergence', 'MACD 顶底背离', '背离', '股票/ETF专用；至少35根K线预热，按同色柱波段比较邻峰与隔峰，跟踪开始、形成、失效。1=有效底背离，-1=有效顶背离，0=无有效背离或方向冲突。', ('close',), {'fast': Parameter('快线周期', 12, 2, 500), 'slow': Parameter('慢线周期', 26, 2, 500), 'signal': Parameter('信号线周期', 9, 2, 500)}, lambda p: max(35, p['slow'] + p['signal']), macd_divergence, 'macd_divergence', {'value': '背离状态', 'diff': 'DIF', 'dea': 'DEA', 'histogram': 'MACD柱'}))

register(Factor('ma', 'MA 均线', '趋势', 'N期算术平均；穿越均线时提示，价格序列同时显示趋势方向。', ('close',), {'n': N()}, WINDOW, moving_average, 'ma', {'value': '均线', 'reference': '原始值', 'bias': '偏离均线 (%)'}))
register(Factor('ema', 'EMA 指数均线', '趋势', '权重 2/(N+1)，首个有效值初始化，缺失时保留上次递推状态。', ('close',), {'n': N()}, WINDOW, exponential_average, 'ma', {'value': 'EMA', 'reference': '原始值', 'bias': '偏离均线 (%)'}))
register(Factor('macd', 'MACD', '趋势', '2 × (DIFF − DEA)，柱线过零时提示。', ('close',), {'fast': N(12), 'slow': N(26), 'signal': N(9)}, lambda p: p['slow'] + p['signal'] - 1, macd, 'macd', {'value': 'MACD柱', 'diff': 'DIFF', 'dea': 'DEA'}))
register(Factor('rsi', 'RSI 相对强弱', '动量', 'Wilder平滑；高低阈值表示超买超卖关注，不自动解释为反向信号。', ('close',), {'n': N(14), 'lower': Parameter('低位阈值', 30, 0, 100), 'upper': Parameter('高位阈值', 70, 0, 100)}, NEXT_WINDOW, rsi, 'rsi', {'value': 'RSI'}))
register(Factor('roc', 'ROC 变化率', '动量', '100 × (当前值 − N期前值) / |N期前值|；支持带负值的普通序列。', ('close',), {'n': N(20), 'threshold': THRESHOLD('关注阈值 (%)', 10)}, NEXT_WINDOW, roc, 'roc', {'value': '变化率 (%)'}))
register(Factor('boll', 'BOLL 布林带', '波动', '标准差除以N；带位置=(当前值−中轨)/(K×标准差)，超过±1提示突破。', ('close',), {'n': N(20), 'k': Parameter('标准差倍数', 2, 0.1, 10, 0.1)}, WINDOW, boll, 'boll', {'value': '带位置', 'mid': '中轨', 'upper': '上轨', 'lower': '下轨', 'reference': '原始值'}))
register(Factor('zscore', 'Z-Score 异常偏离', '统计', '与此前N期均值比较，不包含当期；适合成交额、估值、情绪等普通序列。', ('close',), {'n': N(60), 'threshold': Parameter('偏离阈值', 2, 0.1, 10, 0.1)}, NEXT_WINDOW, zscore, 'zscore', {'value': '标准化偏离'}))
register(Factor('percentile', '历史分位', '统计', '当前值在最近N期的中位排名，常数序列为50%；极端分位仅提示关注。', ('close',), {'n': N(120), 'lower': Parameter('低位阈值 (%)', 10, 0, 100), 'upper': Parameter('高位阈值 (%)', 90, 0, 100)}, WINDOW, lambda d, p: output(percentile(d.close, p['n'])), 'percentile', {'value': '历史分位 (%)'}))
register(Factor('atr', 'ATR 真实波幅', '波动', 'Wilder平滑真实波幅；波幅占收盘价比例超过阈值时关注。', ('high', 'low', 'close'), {'n': N(14), 'threshold': THRESHOLD('波幅占比阈值 (%)', 3)}, NEXT_WINDOW, atr, 'atr', {'value': 'ATR', 'percent': '波幅占比 (%)'}))
register(Factor('kdj', 'KDJ 随机指标', '动量', 'K、D采用SMA(X,3,1)；平价区间分母为零时无信号。', ('high', 'low', 'close'), {'n': N(9), 'lower': Parameter('低位阈值', 20, 0, 100), 'upper': Parameter('高位阈值', 80, 0, 100)}, lambda p: p['n'] + 4, kdj, 'kdj', {'value': 'K', 'd': 'D', 'j': 'J'}))
register(Factor('volume_ratio', '日频量比', '量能', '当期成交量 / 此前N期开市日平均成交量；分母不含当期。', ('volume',), {'n': N(5), 'threshold': Parameter('放量阈值', 2, 0.1, 20, 0.1)}, NEXT_WINDOW, volume_ratio, 'volume_ratio', {'value': '量比'}))
register(Factor('obv', 'OBV 能量潮', '量能', '上涨加量、下跌减量，起点为0；历史缺口使累计值无法准确确定。', ('close', 'volume'), {'n': N(5)}, NEXT_WINDOW, obv, 'obv', {'value': 'OBV', 'change': '区间OBV变化'}))
register(Factor('vr', 'VR 成交量变异率', '量能', 'N期上涨日成交量 / 下跌日成交量 ×100，平盘日排除。', ('close', 'volume'), {'n': N(26), 'lower': Parameter('低位阈值', 70, 0, 500), 'upper': Parameter('高位阈值', 150, 0, 500)}, NEXT_WINDOW, vr, 'vr', {'value': 'VR (%)'}))
register(Factor('volatility', '历史波动率', '波动', '收益率总体标准差 × √年化期数，缺失或零分母不生成信号。', ('close',), {'n': N(20), 'annual': Parameter('年化期数', 252, 1, 366), 'threshold': THRESHOLD('年化波动阈值 (%)', 30)}, NEXT_WINDOW, realized_volatility, 'volatility', {'value': '年化波动率 (%)'}))
