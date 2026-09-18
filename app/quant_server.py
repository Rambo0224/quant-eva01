from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from functools import lru_cache
from threading import Lock
import os
import json

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from app.analysis.data import ROOT, Repository, request_snapshot
from app.analysis.engine import analyze
from app.analysis.factors import REGISTRY
from app.analysis.library import build_library, database_metadata, load_object, FIELD_LABELS
from app.analysis.engine import number

app = FastAPI(title='Quant 量化分析工作台', version='0.1.0')
STATIC = ROOT / 'web/quant'
app.mount('/static', StaticFiles(directory=str(STATIC)), name='quant-static')
repository = Repository()
_lock = Lock()
_catalog_stamp = None
_catalog = []


@app.middleware('http')
async def pin_publication(request, call_next):
    # All database reads in a request use one generation, including analyses
    # that load multiple objects while the updater publishes a new snapshot.
    token = request_snapshot.set((repository.root, repository.path))
    try:
        return await call_next(request)
    finally:
        request_snapshot.reset(token)


@app.on_event('startup')
def prepare_snapshot():
    from datahub.sync.snapshot import ensure_snapshot
    ensure_snapshot(ROOT)


@app.get('/api/data-status')
def data_status():
    def read(path):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
    report = read(ROOT / 'logs/data_sync/latest.json')
    if report.get('status') == 'running':
        pid = report.get('pid')
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
            except OSError:
                report['status'] = 'interrupted'
                report['stage'] = 'interrupted'
    snapshot = read(ROOT / 'data/panel/current.json')
    return {'status': report.get('status'), 'stage': report.get('stage'),
            'target_date': report.get('end'), 'snapshot': snapshot.get('file'),
            'published_at': snapshot.get('published_at')}


def catalog():
    global _catalog_stamp, _catalog
    paths = [repository.path, repository.root / 'config/indicators.yaml', *sorted((repository.root / 'config/modules').glob('*.yaml'))]
    stamp = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths if p.exists())
    with _lock:
        if _catalog_stamp != stamp:
            _catalog = repository.catalog()
            _catalog_stamp = stamp
        return _catalog


def divergence_priority(dataset):
    from datahub.sync.storage import settings as sync_settings
    sources = {source_id: spec.get('priority', 0) for source_id, spec in sync_settings()['sources'].items()}
    return (dataset.last_date, -sources.get(dataset.source, 999), dataset.id)


def analysis_library(datasets):
    instruments, facts = database_metadata(repository)
    return build_library(datasets, repository.root, instruments, facts)


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-cache'})


@app.get('/style.css')
def stylesheet():
    return FileResponse(STATIC / 'style.css', media_type='text/css', headers={'Cache-Control': 'no-cache'})


@app.get('/app.js')
def application_script():
    return FileResponse(STATIC / 'app.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache'})


@app.get('/api/health')
def health():
    return {'status': 'ok', 'app': 'quant', 'project_root': str(ROOT), 'pid': os.getpid(),
            'database_exists': repository.path.exists()}


@lru_cache(maxsize=1)
def plotly_bundle():
    from plotly.offline import get_plotlyjs
    return get_plotlyjs()


@app.get('/static-plotly.js')
def plotly_js():
    return Response(plotly_bundle(), media_type='application/javascript', headers={'Cache-Control': 'public, max-age=86400'})


@app.get('/api/catalog')
def get_catalog():
    try:
        datasets = catalog()
        calendar = repository.calendar()
        objects, aliases = analysis_library(datasets)
    except (duckdb.Error, ValueError, OSError) as exc:
        raise HTTPException(503, '无法读取本地数据，请检查数据库是否被更新程序占用。') from exc
    scalar = [d for d in datasets if d.table in ('observations','core.observations')]
    preferred = ['hs300-close', 'zz500-close', 'turnover-total']
    defaults = [next((d.id for d in scalar if d.filters['indicator_id'] == name), None) for name in preferred]
    return {'datasets': [d.public() for d in datasets], 'objects': objects, 'dataset_object_ids': aliases, 'factors': [{
        'id': f.id, 'name': f.name, 'category': f.category, 'description': f.description,
        'required': f.required, 'parameters': {k: asdict(v) for k, v in f.parameters.items()},
        'dataset_categories': ['股票行情', 'ETF行情'] if f.id == 'macd_divergence' else None,
    } for f in REGISTRY.values()], 'defaults': {'dataset_ids': [d for d in defaults if d],
        'factor_ids': ['ma', 'rsi', 'boll', 'zscore']},
        'calendar_until': calendar.max().strftime('%Y-%m-%d') if len(calendar) else None,
        'snapshot': repository.path.name, 'today': date.today().isoformat()}


class FactorRequest(BaseModel):
    id: str
    params: dict[str, float] = Field(default_factory=dict)


class AnalysisRequest(BaseModel):
    dataset_ids: list[str] = Field(min_length=1, max_length=80)
    factors: list[FactorRequest] = Field(min_length=1, max_length=20)
    start: date
    end: date
    stale_days: int = Field(default=7, ge=1, le=90)

    @model_validator(mode='after')
    def check_request(self):
        if self.start > self.end:
            raise ValueError('开始日期不能晚于结束日期')
        if self.end > date.today():
            raise ValueError('分析截止日不能晚于今天')
        if len(self.dataset_ids) != len(set(self.dataset_ids)):
            raise ValueError('数据选择不能重复')
        if len({f.id for f in self.factors}) != len(self.factors):
            raise ValueError('因子选择不能重复')
        if len(self.dataset_ids) * len(self.factors) > 240:
            raise ValueError('一次最多计算240组数据与因子组合，请缩小选择范围')
        return self


def run_analysis(request: AnalysisRequest, history: bool):
    try:
        index = {d.id: d for d in catalog()}
        objects = {}
        if any(key.startswith('object:') for key in request.dataset_ids):
            objects = {item['id']: item for item in analysis_library(list(index.values()))[0]}
        if set(request.dataset_ids) - (index.keys() | objects.keys()):
            raise ValueError('部分数据已不在目录中，请刷新后重新选择')
        for item in request.factors:
            if item.id not in REGISTRY:
                raise ValueError(f'未知因子：{item.id}')
            REGISTRY[item.id].resolve(item.params)
        calendar = repository.calendar()
        results, issues = [], []
        divergence_sources = {}
        for dataset_id in request.dataset_ids:
            if dataset_id in objects:
                continue
            dataset = index[dataset_id]
            if dataset.table in ('equity_daily_bars','core.market_daily_bars','core.market_daily_bars_by_source'):
                symbol = dataset.filters['symbol'].split('.')[0]
                priority = divergence_priority(dataset)
                if symbol not in divergence_sources or priority > divergence_sources[symbol][0]:
                    divergence_sources[symbol] = (priority, dataset_id)
        for dataset_id in request.dataset_ids:
            bindings = []
            if dataset_id in objects:
                required = {field for factor in request.factors for field in REGISTRY[factor.id].required}
                minimum_history = max(REGISTRY[f.id].lookback(REGISTRY[f.id].resolve(f.params)) for f in request.factors)
                loaded, bindings = load_object(repository, objects[dataset_id], index, request.end.isoformat(), calendar, required, minimum_history)
            else:
                loaded = repository.load(index[dataset_id], request.end.isoformat(), calendar)
            dataset_results = []
            for item in request.factors:
                if dataset_id not in objects and item.id == 'macd_divergence' and loaded.dataset.table in ('equity_daily_bars','core.market_daily_bars','core.market_daily_bars_by_source'):
                    symbol = loaded.dataset.filters['symbol'].split('.')[0]
                    chosen_id = divergence_sources[symbol][1]
                    if chosen_id != dataset_id:
                        continue
                result = analyze(loaded, item.id, item.params, request.start.isoformat(), request.end.isoformat(), request.stale_days, history)
                if bindings:
                    result['data_bindings'] = bindings
                    result['required_fields'] = list(REGISTRY[item.id].required)
                    result['related_data'] = []
                    for binding in bindings:
                        for field in binding['fields']:
                            if field not in loaded.frame:
                                continue
                            series = loaded.frame[field].dropna()
                            result['related_data'].append({'field': field, 'label': binding.get('field_labels', {}).get(field, FIELD_LABELS.get(field, field)),
                                'value': number(series.iloc[-1]) if len(series) else None,
                                'date': str(series.index[-1].date()) if len(series) else None,
                                'source': binding['source'], 'unit': binding.get('field_units', {}).get(field, binding['unit']),
                                'used': field in REGISTRY[item.id].required})
                dataset_results.append(result)
            results.extend(dataset_results)
            problems = sorted({r['status'] for r in dataset_results if r['status'] not in ('正常', '值得关注')})
            notes = list(dict.fromkeys(note for r in dataset_results for note in r['notes']))
            if problems or notes:
                issues.append({'dataset_id': dataset_id, 'name': loaded.dataset.name, 'problems': problems, 'notes': notes,
                    'data_date': next((r['data_date'] for r in dataset_results if r['data_date']), None)})
        results.sort(key=lambda r: (not r['attention'], r['stale'], r['dataset_name'], r['factor_name']))
        return {'results': results, 'issues': issues, 'generated_at': datetime.now().isoformat(timespec='seconds'),
            'start': request.start.isoformat(), 'end': request.end.isoformat(),
            'summary': {'datasets': len(request.dataset_ids), 'combinations': len(results),
                'attention': sum(r['attention'] and not r['stale'] for r in results),
                'historical_attention': sum(r['attention'] and r['stale'] for r in results),
                'invalid': sum(r['status'] not in ('正常', '值得关注') for r in results),
                'stale_datasets': len({r['dataset_id'] for r in results if r['stale']})}}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (duckdb.Error, OSError) as exc:
        raise HTTPException(503, '本地行情暂时无法读取，可能正在更新；请稍后重试。') from exc


@app.post('/api/analyze')
def analyze_api(request: AnalysisRequest):
    return run_analysis(request, history=True)


@app.post('/api/scan')
def scan_api(request: AnalysisRequest):
    return run_analysis(request, history=False)


@app.get('/api/scan-defaults')
def scan_defaults():
    datasets = [d.id for d in catalog() if d.table in ('observations', 'core.observations', 'market_daily_bars', 'core.other_market_daily_bars')]
    return {'dataset_ids': datasets[:80], 'factors': [{'id': 'zscore'}, {'id': 'percentile'}, {'id': 'roc'}]}


@app.get('/api/divergence-defaults')
def divergence_defaults():
    preferred = {}
    for dataset in catalog():
        if dataset.table not in ('equity_daily_bars','core.market_daily_bars','core.market_daily_bars_by_source'):
            continue
        symbol = dataset.filters['symbol'].split('.')[0]
        if not symbol.startswith(('00', '30', '60', '68', '15', '16', '50', '51', '56', '58')):
            continue
        if symbol not in preferred or divergence_priority(dataset) > divergence_priority(preferred[symbol]):
            preferred[symbol] = dataset
    etfs = [dataset.id for dataset in preferred.values() if dataset.category == 'ETF行情'][:40]
    stocks = [dataset.id for dataset in preferred.values() if dataset.category == '股票行情'][:20]
    return {'dataset_ids': etfs + stocks, 'factor_ids': ['macd_divergence'], 'params': {}}
