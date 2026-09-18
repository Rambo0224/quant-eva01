from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace

import pytest

from app import launcher
from datahub.sync import workflow


def test_update_delegates_complete_existing_pipeline_and_restores_panel(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(workflow, 'writer_lock', lambda root: nullcontext())
    reports = iter([{'run_id':'old'}, {'run_id':'new','status':'partial','failed':['gold'],'end':'2025-01-02'}])
    monkeypatch.setattr(launcher,'read_report',lambda:next(reports))
    monkeypatch.setattr(launcher,'stop_panel',lambda port:pytest.fail('panel must stay online'))
    monkeypatch.setattr(launcher,'start_panel',lambda port,open_browser:calls.append(('restore',port,open_browser)))
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['cwd'] == launcher.ROOT
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(launcher.subprocess,'run',run)
    assert launcher.update_data(8600,'2025-01-02') == 1
    assert calls[0] == ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                        str(launcher.ROOT/'update_data.ps1'),'-Dataset','all','-End','2025-01-02']
    assert len(calls) == 1
    assert '部分完成' in capsys.readouterr().out


def test_invalid_update_date_does_not_stop_panel(monkeypatch):
    monkeypatch.setattr(launcher,'stop_panel',lambda port:pytest.fail('must validate first'))
    with pytest.raises(ValueError):
        launcher.update_data(8600,'not-a-date')


def test_update_rejects_a_day_before_market_close(monkeypatch):
    class Clock:
        fromisoformat = staticmethod(date.fromisoformat)
        today = staticmethod(lambda: date(2025,1,2))
    monkeypatch.setattr(launcher,'date',Clock)
    monkeypatch.setattr('datahub.sync.service.default_end',lambda:'2025-01-01')
    with pytest.raises(ValueError,match='尚未完成收盘'):
        launcher.update_data(8600,'2025-01-02')


def test_failed_launch_of_updater_restores_panel(monkeypatch):
    calls=[]
    monkeypatch.setattr(workflow,'writer_lock',lambda root:nullcontext())
    monkeypatch.setattr(launcher,'read_report',lambda:None)
    monkeypatch.setattr(launcher,'stop_panel',lambda port:pytest.fail('panel must stay online'))
    monkeypatch.setattr(launcher,'start_panel',lambda *a,**k:calls.append('restored'))
    def fail(*args, **kwargs):
        raise OSError('cannot launch')
    monkeypatch.setattr(launcher.subprocess,'run',fail)
    with pytest.raises(OSError):
        launcher.update_data(8600)
    assert calls == []


def test_panel_reuses_existing_service_without_updating(monkeypatch):
    monkeypatch.setattr(launcher,'health',lambda port:{'pid':123})
    monkeypatch.setattr(launcher.subprocess,'Popen',lambda *a,**k:pytest.fail('duplicate process'))
    monkeypatch.setattr(launcher,'update_data',lambda *a:pytest.fail('unexpected update'))
    launcher.start_panel(8600,open_browser=False)


def test_old_report_not_reported_as_new_success(monkeypatch,capsys):
    monkeypatch.setattr(workflow,'writer_lock',lambda root:nullcontext())
    monkeypatch.setattr(launcher,'read_report',lambda:{'run_id':'old','status':'success'})
    monkeypatch.setattr(launcher,'stop_panel',lambda port:pytest.fail('panel must stay online'))
    monkeypatch.setattr(launcher.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1))
    assert launcher.update_data(8600) == 1
    output=capsys.readouterr().out
    assert '未生成新报告' in output
    assert '更新成功' not in output
