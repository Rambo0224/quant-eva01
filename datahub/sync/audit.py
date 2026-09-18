"""Inventory-based freshness audit; a global maximum can never prove coverage."""
from __future__ import annotations

from .storage import ROOT, settings


def audit_database(conn, end):
    from macro_replay.config import load_indicators
    from macro_replay.etf_catalog import ETF_WATCHLIST

    config = settings()
    rules = config['rules']
    observations = {r[0]: str(r[1]) if r[1] else None for r in conn.execute(
        'SELECT indicator_id,max(obs_time)::DATE FROM core.observations WHERE isfinite(value) GROUP BY 1').fetchall()}
    raw_observations = dict(conn.execute(
        'SELECT indicator_id,max(obs_time)::DATE FROM observations WHERE isfinite(value) GROUP BY 1').fetchall())
    owners = {}
    for name, rule in rules.items():
        if rule['enabled']:
            for key in rule.get('indicators', []) + rule.get('observation_outputs', []):
                owners[key] = name
    for name, indicators in config.get('derived_indicators', {}).items():
        owners.update({key: name for key in indicators})
    catalog = load_indicators()
    for item in catalog:
        if item.series and item.source != 'catalog':
            owners.setdefault(item.id, 'configured')
    aliases = config.get('indicator_aliases', {})
    rows = []
    def add(domain, key, raw_latest, latest, owner, target=end):
        if owner in rules and not rules[owner]['enabled']:
            owner=None
        status = 'current' if latest and str(latest) >= target else 'stale' if latest else 'missing'
        if not owner:
            status = 'unregistered'
        rows.append(dict(domain=domain, key=key, raw_latest=str(raw_latest) if raw_latest else None,
                         latest=str(latest) if latest else None, expected=target, owner=owner, status=status))
    for key in sorted(set(observations) | {i.id for i in catalog} | set(owners)):
        if key in aliases:
            continue  # Explicit identity alias; canonical indicator is audited instead.
        add('observation', key, raw_observations.get(key), observations.get(key), owners.get(key))
    # Multi-series indicators must not pass because only one component is current.
    raw_components={(i,s):d for i,s,d in conn.execute('SELECT indicator_id,series_id,max(obs_time)::DATE FROM observations WHERE isfinite(value) GROUP BY 1,2').fetchall()}
    components={(i,s):d for i,s,d in conn.execute('SELECT indicator_id,series_id,max(obs_time)::DATE FROM core.observations WHERE isfinite(value) GROUP BY 1,2').fetchall()}
    expected_components=set(raw_components)
    for item in catalog:
        expected_components.update((item.id,f"{item.id}:{part['code']}") for part in item.series or [] if part.get('code'))
    for indicator,series in sorted(expected_components):
        if indicator in aliases: continue
        if sum(i==indicator for i,s in expected_components)>1 or (indicator,series) not in components:
            add('observation_component',series,raw_components.get((indicator,series)),components.get((indicator,series)),owners.get(indicator))
    # Canonical symbols and adjustment are the identity; suppliers are alternatives.
    raw = conn.execute('''SELECT core.canonical_symbol(symbol),s.adjustment,max(obs_time)::DATE
        FROM equity_daily_bars b LEFT JOIN core.source_contracts s ON b.source_id=s.source_id
        GROUP BY 1,2''').fetchall()
    standardized = {(s,a):d for s,a,d in conn.execute(
        'SELECT symbol,price_adjustment,max(trade_date) FROM core.market_daily_bars GROUP BY 1,2').fetchall()}
    exemptions = {}
    universe_file = ROOT / 'data' / 'baostock_universe.csv'
    import pandas as pd
    if universe_file.exists():
        universe = pd.read_csv(universe_file,dtype=str).fillna('')
        for item in universe.to_dict('records'):
            if item.get('universe_date') != end:
                continue
            symbol=conn.execute('SELECT core.canonical_symbol(?)',[item['symbol']]).fetchone()[0]
            if item.get('tradeStatus') == '0':
                exemptions[symbol] = 'verified_suspended'
            elif (symbol,'raw') not in standardized:
                add('market',f'{symbol}:raw',None,None,'baostock_stock')
    metadata_file = ROOT / 'data' / 'baostock_security_status.csv'
    if metadata_file.exists():
        metadata = pd.read_csv(metadata_file,dtype=str).fillna('')
        for item in metadata.to_dict('records'):
            if item.get('verified_on') != end or not item.get('outDate') or item['outDate'] > end:
                continue
            symbol=conn.execute('SELECT core.canonical_symbol(?)',[item['code'].split('.')[-1]]).fetchone()[0]
            exemptions[symbol]='verified_delisted'
    for symbol, adjustment, latest in raw:
        if adjustment == 'unknown' or adjustment is None:
            # Unknown historical price basis stays quarantined; cannot silently count as coverage.
            if not any(s == symbol for s,a in standardized):
                add('market', f'{symbol}:unknown', latest, None, None)
            continue
        owner = ('market_stock' if adjustment == 'qfq' else
                 ('market_etf' if symbol and symbol[:1] in ('1','5') else None) if adjustment=='raw' else None)
        add('market', f'{symbol}:{adjustment}', latest, standardized.get((symbol,adjustment)), owner)
        if symbol in exemptions and rows[-1]['latest']:
            rows[-1]['status']=exemptions[symbol]
    for item in ETF_WATCHLIST:
        code = str(item['code']).zfill(6)
        symbol = conn.execute('SELECT core.canonical_symbol(?)',[code]).fetchone()[0]
        if (symbol,'raw') not in standardized:
            add('market', f'{symbol}:raw', None, None, 'market_etf')
    raw_shares = dict(conn.execute('SELECT core.canonical_symbol(symbol),max(obs_time)::DATE FROM etf_share_daily GROUP BY 1').fetchall())
    core_shares = dict(conn.execute('''SELECT i.symbol,max(f.trade_date) FROM core.etf_daily_facts f
        JOIN core.instrument_master i USING(instrument_id) GROUP BY 1''').fetchall())
    wanted = set(raw_shares) | {conn.execute('SELECT core.canonical_symbol(?)',[str(x['code']).zfill(6)]).fetchone()[0] for x in ETF_WATCHLIST}
    for symbol in sorted(wanted):
        add('etf_shares',symbol,raw_shares.get(symbol),core_shares.get(symbol),'etf_shares')
    for asset, latest in conn.execute('SELECT asset_id,max(obs_time)::DATE FROM market_daily_bars GROUP BY 1').fetchall():
        core_latest = conn.execute('SELECT max(obs_time)::DATE FROM core.other_market_daily_bars WHERE asset_id=?',[asset]).fetchone()[0]
        add('other_market',asset,latest,core_latest,'gold' if asset=='london-gold-spot' else None)
    # A newer date does not erase holes from a partially failed daily refresh.
    import json
    state=conn.execute("SELECT report FROM raw.sync_state WHERE rule_id='baostock_stock'").fetchone()
    if state:
        pending=json.loads(state[0]).get('result',{}).get('failed',[])
        for failure in pending:
            day=failure.get('date')
            if not day or day>end: continue
            has_manifest=conn.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='raw' AND table_name='daily_market_manifest'").fetchone()
            verified=False
            if has_manifest:
                from scripts.fetch_baostock_equity_bars import _verified_complete_day
                verified=_verified_complete_day(conn,'baostock_history_k_data',day)
            if not verified:
                add('market_day',day,None,None,'baostock_stock',target=day)
                rows[-1]['status']='unverified_coverage'
                rows[-1]['reason']=failure.get('error')
    exceptions = [r for r in rows if r['status'].startswith('verified_')]
    issues = [r for r in rows if r['status'] != 'current' and not r['status'].startswith('verified_')]
    return dict(target_date=end, total=len(rows), current=sum(r['status']=='current' for r in rows),
                all_current=not issues, issues=issues, exceptions=exceptions, aliases=aliases, inventory=rows)
