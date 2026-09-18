import subprocess
import sys

import duckdb
import pytest

from app.analysis.data import Repository
from datahub.sync import snapshot


def test_reader_survives_separate_writer_and_generation_switch(tmp_path):
    (tmp_path / 'data').mkdir()
    source = tmp_path / 'data/replay.duckdb'
    with duckdb.connect(str(source)) as c:
        c.execute('CREATE TABLE values_table AS SELECT 1 AS n')
    snapshot.publish_snapshot(tmp_path)
    repo = Repository(tmp_path)
    old = repo.connection()
    script = "import duckdb,sys; c=duckdb.connect(sys.argv[1]); c.execute('UPDATE values_table SET n=2'); print('ready',flush=True); input(); c.close()"
    writer = subprocess.Popen([sys.executable, '-c', script, str(source)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert writer.stdout.readline().strip() == 'ready'
        with repo.connection() as c:
            assert c.execute('SELECT n FROM values_table').fetchone() == (1,)
    finally:
        writer.communicate('\n', timeout=15)
    snapshot.publish_snapshot(tmp_path)
    assert old.execute('SELECT n FROM values_table').fetchone() == (1,)
    with repo.connection() as c:
        assert c.execute('SELECT n FROM values_table').fetchone() == (2,)
    old.close()


def test_failed_copy_preserves_published_snapshot(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    with duckdb.connect(str(tmp_path / 'data/replay.duckdb')) as c:
        c.execute('CREATE TABLE t AS SELECT 1')
    snapshot.publish_snapshot(tmp_path)
    previous = snapshot.current_snapshot(tmp_path)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(snapshot.shutil, 'copyfile', fail)
    with pytest.raises(OSError, match='disk full'):
        snapshot.publish_snapshot(tmp_path)
    assert snapshot.current_snapshot(tmp_path) == previous
