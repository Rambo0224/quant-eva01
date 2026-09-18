"""Stable orchestration API: acquire -> raw storage -> normalization -> report."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from .storage import ROOT, settings


@contextmanager
def writer_lock(root: Path = ROOT):
    """OS-released lock: a crashed process cannot leave a stale lock behind."""
    directory = root / '.work'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'data_sync.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Another data workflow is running; retry after it finishes') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def write_report(report, root=ROOT):
    directory = root / 'logs' / 'data_sync'
    directory.mkdir(parents=True, exist_ok=True)
    report['updated_at'] = datetime.now(timezone.utc).isoformat()
    content = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    for destination in (directory / f"{report['run_id']}.json", directory / 'latest.json'):
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(destination)


def run_pipeline(end: str, datasets=None, symbols=None, *, root=ROOT):
    """Run independently configured datasets; always publish other usable data.

    Partial success is explicit. Neither an unavailable supplier nor a stale
    sequence is reported as fully current. This API does not start a web server.
    """
    from .service import normalize, run_rule, derived_steps
    end = datetime.strptime(end, '%Y-%m-%d').date().isoformat()
    rules = settings()['rules']
    names = list(datasets) if datasets is not None else [key for key, value in rules.items() if value['enabled']]
    if not names or len(names) != len(set(names)):
        raise ValueError('Select at least one distinct dataset')
    for name in names:
        if name not in rules or not rules[name]['enabled']:
            raise ValueError(f'Unknown or disabled dataset: {name}')
    report = {'run_id': str(uuid.uuid4()), 'pid': os.getpid(), 'started_at': datetime.now(timezone.utc).isoformat(),
              'end': end, 'status': 'running', 'stage': 'acquire', 'datasets': [], 'failed': []}
    with writer_lock(root):
        from .snapshot import current_snapshot, publish_snapshot
        if (root / 'data/replay.duckdb').exists() and current_snapshot(root) is None:
            publish_snapshot(root)
        write_report(report, root)
        for name in names:
            report['active_dataset'] = name
            write_report(report, root)
            try:
                result = run_rule(name, end, symbols)
            except Exception as exc:
                result = {'rule': name, 'failed': [{'error': str(exc)}]}
            report['datasets'].append(result)
            if result.get('failed'):
                report['failed'].append(name)
            report.pop('active_dataset', None)
            write_report(report, root)
        if datasets is None:
            report['stage'] = 'derive'
            report['derived'] = []
            write_report(report, root)
            for name, function, args in derived_steps():
                try:
                    result = function(*args)
                    if isinstance(result, dict) and result.get('failed'):
                        report['failed'].append(name)
                except Exception as exc:
                    result = {'failed': [{'error': str(exc)}]}
                    report['failed'].append(name)
                report['derived'].append({'name': name, 'result': result})
                write_report(report, root)
        report['stage'] = 'normalize'
        write_report(report, root)
        try:
            report['normalization'] = normalize(end)
            if (root / 'data/replay.duckdb').exists():
                report['panel_snapshot'] = publish_snapshot(root)
            report['status'] = 'partial' if report['failed'] or not report['normalization'].get('all_current', False) else 'success'
        except Exception as exc:
            report['normalization'] = {'status': 'failed', 'error': str(exc)}
            report['failed'].append('normalize')
            report['status'] = 'failed'
        report['stage'] = 'finished'
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_report(report, root)
    return report
