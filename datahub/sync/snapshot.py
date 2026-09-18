"""Immutable panel snapshots; only the small manifest is atomically replaced."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import uuid
import time

import duckdb

from .storage import ROOT


def current_snapshot(root=ROOT):
    directory = Path(root) / 'data/panel'
    manifest = directory / 'current.json'
    if not manifest.exists():
        return None
    data = json.loads(manifest.read_text(encoding='utf-8'))
    path = directory / Path(data['file']).name
    if not path.is_file():
        raise RuntimeError('Published panel snapshot is missing')
    return path


def publish_snapshot(root=ROOT):
    """Caller holds writer_lock; checkpoint and copy with no concurrent writes.

    A new filename lets existing Windows readers finish on the old generation.
    Failure before manifest replacement leaves the previous publication intact.
    """
    root = Path(root)
    source = root / 'data/replay.duckdb'
    directory = root / 'data/panel'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{uuid.uuid4().hex}.duckdb'
    temporary = directory / 'current.tmp'
    try:
        with duckdb.connect(str(source)) as conn:
            conn.execute('CHECKPOINT')
        # Windows DuckDB uses an exclusive file handle even for copying.
        # writer_lock prevents another managed writer after this close.
        shutil.copyfile(source, target)
        with duckdb.connect(str(target), read_only=True) as conn:
            conn.execute('SELECT count(*) FROM information_schema.tables').fetchone()
        record = {'file': target.name, 'published_at': datetime.now(timezone.utc).isoformat()}
        temporary.write_text(json.dumps(record), encoding='utf-8')
        temporary.replace(directory / 'current.json')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    # Keep two generations; locked old files are harmless and retried next time.
    old = sorted(directory.glob('*.duckdb'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in old[2:]:
        if time.time() - path.stat().st_mtime < 86400:
            continue
        try:
            path.unlink()
        except OSError:
            pass
    return record


def ensure_snapshot(root=ROOT):
    if current_snapshot(root) is None:
        from .workflow import writer_lock
        with writer_lock(root):
            if current_snapshot(root) is None:
                publish_snapshot(root)
