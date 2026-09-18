"""Analysis objects are a semantic catalog over canonical merged series."""
from dataclasses import replace

import pandas as pd
import yaml

from .data import LoadedData, identity

FIELD_LABELS = {'open': '开盘价', 'high': '最高价', 'low': '最低价', 'close': '收盘价',
                'volume': '成交量', 'amount': '成交额', 'turnover_rate': '换手率',
                'shares': '基金份额', 'share_change': '份额变化',
                'estimated_flow_amount': '估算资金流', 'pe': '市盈率', 'pb': '市净率',
                'vol-20d': '20日波动率', 'rsi-14d': '14日RSI', 'basis': '基差', 'basis-annual': '年化基差'}
BAR_TABLES = ('equity_daily_bars', 'core.market_daily_bars', 'core.market_daily_bars_by_source',
              'market_daily_bars', 'core.other_market_daily_bars')


def symbol_code(value):
    parts = str(value).upper().split('.')
    return next((part for part in parts if part.isdigit()), str(value))


def build_library(datasets, root, instruments=(), facts=()):
    path = root / 'config/analysis_objects.yaml'
    definitions = (yaml.safe_load(path.read_text(encoding='utf-8')) or {}).get('objects', {}) if path.exists() else {}
    relations = {indicator: (key, field) for key, item in definitions.items()
                 for field, indicator in item.get('series', {}).items()}
    assets = {asset: key for key, item in definitions.items() for asset in item.get('asset_ids', [])}
    masters = {symbol_code(row['symbol']): row for row in instruments}
    groups, aliases = {}, {}
    for dataset in datasets:
        indicator = dataset.filters.get('indicator_id')
        code = symbol_code(dataset.filters.get('symbol', ''))
        if code:
            key, field = 'security:' + code, None
        elif indicator in relations:
            key, field = relations[indicator]
        elif dataset.table in BAR_TABLES:
            asset = dataset.filters['asset_id']
            key, field = assets.get(asset, 'market:' + asset), None
        else:
            # Unmapped macro series remain independent; never infer by fuzzy names.
            key, field = 'series:' + str(indicator) + ':' + dataset.unit, 'close'
        master = masters.get(code, {})
        title = definitions.get(key, {}).get('name') or (f"{master['name']} · {code}" if master.get('name') else dataset.name)
        entry = groups.setdefault(key, {'id': 'object:' + identity(key), 'name': title,
            'category': definitions.get(key, {}).get('category', dataset.category),
            'symbol': master.get('symbol', code), 'instrument_id': master.get('instrument_id'),
            'members': [], 'fields': [], 'facts': [], 'first_date': dataset.first_date,
            'last_date': dataset.last_date, 'rows': 0, 'source': '', 'adjustment': '自动匹配，保留原始口径'})
        entry['members'].append({'dataset_id': dataset.id, 'field': field})
        entry['fields'] += [field] if field else dataset.fields
        entry['first_date'] = min(entry['first_date'], dataset.first_date)
        entry['last_date'] = max(entry['last_date'], dataset.last_date)
        entry['rows'] = max(entry['rows'], dataset.rows)
        aliases[dataset.id] = entry['id']
    by_instrument = {item['instrument_id']: item for item in groups.values() if item['instrument_id']}
    for fact in facts:
        item = by_instrument.get(fact['instrument_id'])
        if item:
            item['facts'].append(fact)
            item['fields'] += fact['fields']
    index = {d.id: d for d in datasets}
    for item in groups.values():
        item['fields'] = sorted(set(item['fields']))
        item['field_labels'] = [FIELD_LABELS.get(f, f) for f in item['fields']]
        if not any(index[m['dataset_id']].price for m in item['members']):
            item['field_labels'] = [item['name'] if f == 'close' else FIELD_LABELS.get(f, f) for f in item['fields']]
        sources = {source.strip() for m in item['members'] for source in index[m['dataset_id']].source.split(',')}
        item['source'] = ', '.join(sorted(sources | {f['source_id'] for f in item['facts']}))
        item['data_count'] = len(item['members']) + len(item['facts'])
    return list(groups.values()), aliases


def database_metadata(repository):
    with repository.connection() as conn:
        tables = {row[0] for row in conn.execute("SELECT table_schema || '.' || table_name FROM information_schema.tables").fetchall()}
        instruments = conn.execute('SELECT instrument_id,symbol,name FROM core.instrument_master').df().to_dict('records') if 'core.instrument_master' in tables else []
        facts = []
        if 'core.etf_daily_facts' in tables:
            rows = conn.execute('''SELECT instrument_id,string_agg(DISTINCT source_id, ', ' ORDER BY source_id),min(trade_date),max(trade_date),
                count(shares),count(share_change),count(estimated_flow_amount),
                string_agg(DISTINCT shares_unit, ', '),string_agg(DISTINCT amount_unit, ', ')
                FROM core.etf_daily_facts GROUP BY 1''').fetchall()
            for instrument, source, first, last, shares, change, flow, shares_unit, amount_unit in rows:
                facts.append({'instrument_id': instrument, 'source_id': source, 'first_date': str(first), 'last_date': str(last),
                    'fields': [field for field, count in zip(('shares','share_change','estimated_flow_amount'), (shares,change,flow)) if count],
                    'shares_unit': shares_unit, 'amount_unit': amount_unit})
    return instruments, facts


def load_object(repository, item, index, end, calendar, required=('close',), minimum_history=1):
    members = [(index[m['dataset_id']], m['field']) for m in item['members']]
    price = [(d, f) for d, f in members if f in (None, 'close')]
    if not price:
        raise ValueError(f"{item['name']} 没有基础价格或数值序列")
    # Inspect only observations on/before cutoff. Prefer a complete usable panel,
    # then its actual latest observation; never rank by future catalog dates.
    from datahub.sync.storage import settings as sync_settings
    priority = {source_id: spec.get('priority', 0) for source_id, spec in sync_settings()['sources'].items()}
    candidates = [(d, field, repository.load(d, end, calendar)) for d, field in price]
    def rank(candidate):
        dataset, field, loaded = candidate
        needed = set(required) & set(loaded.frame.columns)
        valid = loaded.frame.dropna(subset=sorted(needed | {'close'}))
        latest = valid.index.max() if len(valid) else pd.Timestamp.min
        tail = loaded.frame.tail(minimum_history)
        enough = len(tail) >= minimum_history and tail[sorted(needed | {'close'})].notna().all().all()
        return (len(needed), enough, latest, field is None, dataset.adjustment == '前复权',
                -priority.get(dataset.source, 999), dataset.id)
    chosen, _, loaded = max(candidates, key=rank)
    frame, notes = loaded.frame.copy(), list(loaded.notes)
    bindings = [{'fields': chosen.fields, 'dataset_id': chosen.id, 'source': chosen.source,
                 'adjustment': chosen.adjustment, 'unit': chosen.unit,
                 'field_labels': {'close': chosen.name} if not chosen.price else {}}]
    if chosen.table in ('core.market_daily_bars','core.market_daily_bars_by_source'):
        with repository.connection() as conn:
            source_filter = ' AND source_id=?' if 'source_id' in chosen.filters else ''
            params = [chosen.filters['symbol']]
            if source_filter:
                params.append(chosen.filters['source_id'])
            params += [chosen.filters['price_adjustment'], end]
            units = conn.execute(f'''SELECT currency,volume_unit,amount_unit,turnover_rate_unit
                FROM {chosen.table} WHERE symbol=?{source_filter}
                AND price_adjustment=? AND trade_date<=CAST(? AS DATE) ORDER BY trade_date DESC LIMIT 1''',params).fetchone()
        if units:
            bindings[0]['field_units'] = dict(zip(('open','high','low','close','volume','amount','turnover_rate'),
                                                [units[0]] * 4 + list(units[1:])))
    if len(candidates) > 1:
        notes.append(f'已按字段需求、最近{minimum_history}期完整性与截止日选择标准序列（{chosen.adjustment}）。')
    for dataset, field in members:
        if field and field != 'close':
            related = repository.load(dataset, end, calendar)
            frame[field] = related.frame.close.reindex(frame.index)
            bindings.append({'fields': [field], 'dataset_id': dataset.id, 'source': dataset.source,
                             'unit': dataset.unit, 'adjustment': dataset.adjustment})
    if item['facts']:
        # Standard facts may switch source by date; no future filling.
        with repository.connection() as conn:
            for field in ('shares','share_change','estimated_flow_amount'):
                candidates = [f for f in item['facts'] if field in f['fields']]
                if not candidates:
                    continue
                fact = sorted(candidates, key=lambda f: f['source_id'])[0]
                records = conn.execute(f'''SELECT trade_date,{field} FROM core.etf_daily_facts
                    WHERE instrument_id=? AND trade_date<=CAST(? AS DATE)
                    QUALIFY row_number() OVER(PARTITION BY trade_date ORDER BY ingested_at DESC)=1
                    ORDER BY trade_date''', [item['instrument_id'], end]).df()
                records.trade_date = pd.to_datetime(records.trade_date)
                frame[field] = records.set_index('trade_date')[field].reindex(frame.index)
                unit = fact['amount_unit'] if field == 'estimated_flow_amount' else fact['shares_unit']
                bindings.append({'fields': [field], 'source': fact['source_id'], 'unit': unit, 'adjustment': '原始数据'})
                if unit and 'unverified' in unit:
                    notes.append(f"{FIELD_LABELS[field]}的来源单位尚未核验，不与成交量换算或混算。")
    descriptor = replace(chosen, id=item['id'], name=item['name'], fields=[f for f in item['fields'] if f in frame])
    return LoadedData(descriptor, frame, notes), bindings
