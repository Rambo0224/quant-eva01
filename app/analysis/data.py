from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml
from contextvars import ContextVar

ROOT = Path(__file__).resolve().parents[2]
request_snapshot = ContextVar('request_snapshot', default=None)


@dataclass
class Dataset:
    id: str
    name: str
    category: str
    source: str
    unit: str
    fields: list[str]
    rows: int
    first_date: str
    last_date: str
    price: bool
    calendar: str
    adjustment: str
    table: str
    filters: dict = field(repr=False)

    def public(self):
        return {k: v for k, v in vars(self).items() if k not in ('table', 'filters')}


@dataclass
class LoadedData:
    dataset: Dataset
    frame: pd.DataFrame
    notes: list[str]


def identity(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:20]


def metadata(root: Path) -> dict:
    result = {}
    paths = [root / 'config/indicators.yaml', *sorted((root / 'config/modules').glob('*.yaml'))]
    for path in paths:
        if not path.exists():
            continue
        content = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        for theme in content.get('themes', {}).values():
            for item in theme.get('indicators', []):
                result[item['id']] = {'name': item.get('title', item['id']), 'category': theme.get('title', '数据序列')}
    return result


class Repository:
    """No source refresh, schema mutation, source mixing or writes to market data."""

    def __init__(self, root: Path = ROOT):
        self.root = Path(root)

    @property
    def path(self):
        pinned = request_snapshot.get()
        if pinned and pinned[0] == self.root:
            return pinned[1]
        from datahub.sync.snapshot import current_snapshot
        return current_snapshot(self.root) or self.root / 'data/replay.duckdb'

    def connection(self):
        if not self.path.exists():
            raise ValueError('未找到行情数据库 data/replay.duckdb')
        return duckdb.connect(str(self.path), read_only=True)

    def calendar(self) -> pd.DatetimeIndex:
        path = self.root / 'data/baostock_trade_dates.csv'
        if not path.exists():
            return pd.DatetimeIndex([])
        frame = pd.read_csv(path)
        return pd.DatetimeIndex(pd.to_datetime(frame.loc[frame.is_trading_day.eq(1), 'calendar_date'])).sort_values().unique()

    def catalog(self) -> list[Dataset]:
        descriptions = metadata(self.root)
        names = {}
        universe = self.root / 'data/baostock_universe.csv'
        if universe.exists():
            records = pd.read_csv(universe, dtype=str)
            names = dict(zip(records.symbol, records.code_name))
        result = []
        with self.connection() as conn:
            tables = {r[0] for r in conn.execute('SHOW TABLES').fetchall()}
            observation_table = 'core.observations' if conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='core' AND table_name='observations'").fetchone() else 'observations'
            if 'observations' in tables:
                rows = conn.execute(f'''SELECT indicator_id,series_id,coalesce(unit,'') unit,
                    string_agg(DISTINCT source_id, ', '),count(DISTINCT obs_time),min(obs_time),max(obs_time)
                    FROM {observation_table} GROUP BY indicator_id,series_id,coalesce(unit,'') ORDER BY indicator_id,series_id,unit''').fetchall()
                for indicator, series, unit, source, count, first, last in rows:
                    info = descriptions.get(indicator, {'name': indicator, 'category': '其他序列'})
                    # Keep units separate; legacy scalar price histories may explicitly splice sources.
                    result.append(Dataset(identity('observations', indicator, series, unit), info['name'], info['category'], source or '', unit,
                        ['close'], count, str(first.date()), str(last.date()), indicator.endswith('-close'),
                        'observed' if 'gold' in indicator else 'CN', '原始序列', observation_table,
                        {'indicator_id': indicator, 'series_id': series, 'unit': unit}))
            has_core = bool(conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='core' AND table_name='market_daily_bars'").fetchone())
            if has_core:
                rows = conn.execute("""SELECT symbol,string_agg(DISTINCT source_id, ', ' ORDER BY source_id),price_adjustment,asset_type,
                    count(*),min(trade_date),max(trade_date),count(volume),max(abs(volume)),count(amount),count(turnover_rate)
                    FROM core.market_daily_bars GROUP BY 1,3,4 ORDER BY symbol,price_adjustment""").fetchall()
                for symbol, sources, adj, asset, count, first, last, vol_count, max_vol, amount_count, turnover_count in rows:
                    code = symbol.split('.')[0]
                    fields = ['open','high','low','close'] + (['volume'] if vol_count and max_vol else []) + (['amount'] if amount_count else []) + (['turnover_rate'] if turnover_count else [])
                    result.append(Dataset(identity('core',symbol,adj),f'{names.get(code,code)} · {code}',
                        'ETF行情' if asset=='etf' else '股票行情',sources,'CNY；成交量：股/份',fields,count,
                        str(first),str(last),True,'CN',{'raw':'不复权','qfq':'前复权','hfq':'后复权'}.get(adj,adj),
                        'core.market_daily_bars',{'symbol':symbol,'price_adjustment':adj}))
            for table, key in [('market_daily_bars', 'asset_id'), ('equity_daily_bars', 'symbol')]:
                if has_core and table == 'equity_daily_bars':
                    continue
                if table not in tables:
                    continue
                if has_core and table == 'market_daily_bars':
                    rows = conn.execute('''SELECT asset_id,string_agg(DISTINCT source_id, ', ' ORDER BY source_id),
                        count(DISTINCT obs_time),min(obs_time),max(obs_time),count(open),count(high),count(low),
                        count(close),count(volume),max(abs(volume)) FROM core.other_market_daily_bars
                        GROUP BY asset_id ORDER BY asset_id''').fetchall()
                    for key_value, sources, count, first, last, op, hi, lo, cl, vo, max_volume in rows:
                        fields = [name for name, n in zip(('open','high','low','close','volume'), (op,hi,lo,cl,vo)) if n]
                        if not max_volume and 'volume' in fields:
                            fields.remove('volume')
                        title = '伦敦金现 · OHLC' if key_value == 'london-gold-spot' else key_value
                        result.append(Dataset(identity('core.other_market',key_value),title,'其他市场行情',sources,
                            '行情原始单位',fields,count,str(first.date()),str(last.date()),True,'observed','原始行情',
                            'core.other_market_daily_bars',{'asset_id':key_value}))
                    continue
                query_table = 'core.other_market_daily_bars' if has_core and table=='market_daily_bars' else table
                rows = conn.execute(f'''SELECT {key},source_id,coalesce(json_extract_string(extra,'$.adjustflag'),'unknown') adj,
                    count(DISTINCT obs_time),min(obs_time),max(obs_time),
                    count(open),count(high),count(low),count(close),count(volume),max(abs(volume))
                    FROM {query_table} GROUP BY {key},source_id,adj ORDER BY {key},source_id,adj''').fetchall()
                for key_value, source, adj, count, first, last, op, hi, lo, cl, vo, max_volume in rows:
                    fields = [name for name, n in zip(('open','high','low','close','volume'), (op,hi,lo,cl,vo)) if n]
                    if not max_volume and 'volume' in fields:
                        fields.remove('volume')
                    symbol = key_value.split('.')[0]
                    is_etf = 'etf' in source
                    title = '伦敦金现 · OHLC' if key_value == 'london-gold-spot' else f'{names.get(symbol, symbol)} · {symbol}'
                    adjustment = {'1': '后复权', '2': '前复权', '3': '不复权'}.get(adj, '口径待核对')
                    if 'qfq' in source:
                        adjustment = '前复权'
                    if table == 'market_daily_bars':
                        adjustment = '原始行情'
                    result.append(Dataset(identity(table, key_value, source, adj), title,
                        'ETF行情' if is_etf else '股票行情' if table == 'equity_daily_bars' else '其他市场行情', source, '行情原始单位',
                        fields, count, str(first.date()), str(last.date()), True,
                        'CN' if table == 'equity_daily_bars' else 'observed', adjustment, query_table,
                        {key: key_value, 'source_id': source, 'adjustment': adj}))
        return result

    def load(self, dataset: Dataset, end: str, calendar: pd.DatetimeIndex | None = None) -> LoadedData:
        where, parameters = [], []
        for key, value in dataset.filters.items():
            column = "coalesce(unit,'')" if key == 'unit' else "coalesce(json_extract_string(extra,'$.adjustflag'),'unknown')" if key == 'adjustment' else key
            where.append(f'{column} = ?')
            parameters.append(value)
        time_column = 'trade_date' if dataset.table in ('core.market_daily_bars','core.market_daily_bars_by_source') else 'obs_time'
        where.append(f'{time_column} < CAST(? AS DATE) + INTERVAL 1 DAY')
        parameters.append(end)
        columns = 'value AS close, extra, source_id' if dataset.table in ('observations','core.observations') else 'open,high,low,close,volume,extra,source_id'
        if dataset.table in ('core.market_daily_bars','core.market_daily_bars_by_source'):
            columns = 'open,high,low,close,volume,amount,turnover_rate,extra,source_id'
        with self.connection() as conn:
            frame = conn.execute(f'''SELECT {time_column} AS date,{columns} FROM {dataset.table}
                WHERE {' AND '.join(where)} QUALIFY row_number() OVER(PARTITION BY {time_column} ORDER BY ingested_at DESC,source_id) = 1
                ORDER BY {time_column}''', parameters).df()
        notes = []
        if ', ' in dataset.source:
            notes.append('原始序列包含多个来源的历史接续；来源切换可能影响计算。')
        if dataset.adjustment in ('不复权', '口径待核对'):
            notes.append(f'价格{dataset.adjustment}；除权除息可能造成机械跳变，请结合公司事件判断。')
        if frame.empty:
            return LoadedData(dataset, pd.DataFrame(columns=['close'], index=pd.DatetimeIndex([], name='date')), notes)
        frame.date = pd.to_datetime(frame.date).dt.normalize()
        frame = frame.drop_duplicates('date', keep='last').set_index('date')
        for field_name in ('open', 'high', 'low', 'close', 'volume', 'amount', 'turnover_rate'):
            if field_name in frame:
                frame[field_name] = pd.to_numeric(frame[field_name], errors='coerce').replace([np.inf, -np.inf], np.nan)
        def suspended(value):
            try:
                meta = json.loads(value) if isinstance(value, str) else value or {}
                return str(meta.get('tradestatus', '')) == '0' or meta.get('suspended') is True
            except (ValueError, TypeError):
                return False
        frame['suspended'] = frame.extra.map(suspended)
        calendar = self.calendar() if calendar is None else calendar
        if dataset.calendar == 'CN' and len(calendar):
            first, last = frame.index.min(), min(frame.index.max(), calendar.max())
            if first <= last:
                expected = calendar[(calendar >= first) & (calendar <= last)]
                # Preserve observations outside available calendar coverage; don't invent future sessions.
                outside = frame.index[(frame.index < calendar.min()) | (frame.index > calendar.max())]
                frame = frame.reindex(expected.union(outside).sort_values())
            if pd.Timestamp(end) > calendar.max():
                notes.append(f'本地交易日历仅覆盖至 {calendar.max():%Y-%m-%d}，之后无法完整核验缺失交易日。')
        else:
            notes.append('按来源观测日期计算，尚无该市场独立交易日历用于核验漏数。')
        frame['suspended'] = frame.suspended.fillna(False).astype(bool)
        # Only explicit suspension records can be filled. Ordinary missing data stays missing.
        previous = frame.close.where(~frame.suspended).ffill()
        for name in ('open', 'high', 'low', 'close'):
            if name in frame:
                frame.loc[frame.suspended, name] = previous.loc[frame.suspended]
        if 'volume' in frame:
            frame.loc[frame.suspended, 'volume'] = 0.0
        missing = int(frame.close.isna().sum())
        if missing:
            notes.append(f'存在 {missing} 个开市日数据缺口；未确认停牌，不补造行情。')
        if frame.suspended.any():
            notes.append(f'{int(frame.suspended.sum())} 个已确认停牌日计入窗口，价格沿用前收盘、成交量为零。')
        return LoadedData(dataset, frame, notes)
