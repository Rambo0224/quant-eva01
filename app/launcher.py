"""Windows desktop entry point. Data acquisition remains in datahub.sync."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def health(port):
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            result = json.load(response)
        if Path(result.get('project_root', '')).resolve() == ROOT and result.get('app') == 'quant':
            return result
        raise RuntimeError(f'端口 {port} 已被其他服务占用，未关闭该服务。')
    except (URLError, TimeoutError, OSError):
        return None


@contextmanager
def operation_lock():
    directory = ROOT / '.work'
    directory.mkdir(exist_ok=True)
    with (directory / 'quant_launcher.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        import msvcrt
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('另一个启动器正在启动或更新，请等待它完成。') from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def start_panel(port, open_browser=True):
    existing = health(port)
    if not existing:
        from datahub.sync.snapshot import ensure_snapshot
        ensure_snapshot(ROOT)
        logs = ROOT / 'logs' / 'launcher'
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / 'panel.out.log').open('ab') as out, (logs / 'panel.err.log').open('ab') as err:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.quant_server:app',
                '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'面板启动失败，请查看 {logs / "panel.err.log"}')
            if health(port):
                break
            time.sleep(.25)
        else:
            process.terminate()
            process.wait(timeout=10)
            raise RuntimeError('面板启动超时，请查看 logs/launcher/panel.err.log。')
    url = f'http://127.0.0.1:{port}/'
    print(f'分析面板已启动：{url}', flush=True)
    if open_browser:
        webbrowser.open(url)


def stop_panel(port):
    current = health(port)
    if not current:
        return False
    pid = int(current['pid'])
    if pid <= 0 or pid == os.getpid():
        raise RuntimeError('无法确认面板进程，未执行关闭。')
    subprocess.run(['taskkill', '/PID', str(pid), '/F'], check=True, capture_output=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not health(port):
            return True
        time.sleep(.2)
    raise RuntimeError('面板尚未退出，未开始更新。')


def read_report():
    path = ROOT / 'logs/data_sync/latest.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def show_report(report=None):
    report = read_report() if report is None else report
    if not report:
        print('尚无数据更新报告。')
        return
    labels = {'success': '更新成功', 'partial': '部分完成，仍有失败或未追平的数据',
              'failed': '更新失败', 'running': '正在更新或上次运行被中断'}
    print('\n' + labels.get(report.get('status'), str(report.get('status'))))
    print(f"目标日期：{report.get('end', '未知')}  阶段：{report.get('stage', '未知')}")
    if report.get('failed'):
        print('未完成的组：' + '、'.join(report['failed']))
    print(f"详细报告：{ROOT / 'logs/data_sync/latest.json'}")


def update_data(port, end=''):
    if end:
        parsed = date.fromisoformat(end)
        if parsed.isoformat() != end or parsed > date.today():
            raise ValueError('截止日须为 YYYY-MM-DD，且不能晚于今天。')
        if parsed == date.today():
            from datahub.sync.service import default_end
            latest_completed = date.fromisoformat(default_end())
            if parsed > latest_completed:
                raise ValueError(f'截止日 {end} 尚未完成收盘；当前可更新至 {latest_completed.isoformat()}。')
    previous = read_report()
    # The panel reads a separate immutable publication throughout acquisition.
    print('正在执行已有完整更新流程：获取 → 原始入库 → 规范化整理 → 报告。', flush=True)
    print('更新期间请保留此窗口；各数据源的范围、优先级、重试和存储规则沿用现有配置。', flush=True)
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
               '-File', str(ROOT / 'update_data.ps1'), '-Dataset', 'all']
    if end:
        command += ['-End', end]
    try:
        result = subprocess.run(command, cwd=ROOT, check=False)
        report = read_report()
        if report and report.get('run_id') != (previous or {}).get('run_id'):
            show_report(report)
        else:
            print('本次未生成新报告，以上控制台输出为准；不会把旧报告当作本次结果。')
        if result.returncode:
            print(f'更新程序返回非零退出码 {result.returncode}；请查看失败或缺口详情。')
        return result.returncode
    finally:
        print('更新命令已结束；面板继续提供最近一次成功发布的数据。', flush=True)


def menu(port):
    while True:
        print('\n========== Quant 量化工作台 ==========')
        print('1  启动分析面板（不更新数据）')
        print('2  更新数据（提取、存放、整理完整流程）')
        print('3  查看最近更新报告')
        print('4  关闭分析面板服务')
        print('0  退出启动器（已启动的面板继续运行）')
        choice = input('请选择：').strip()
        try:
            if choice == '0':
                return 0
            if choice == '3':
                show_report()
            elif choice in ('1', '2', '4'):
                end = input('更新截止日 YYYY-MM-DD（回车沿用前一工作日规则，节假日请指定交易日）：').strip() if choice == '2' else ''
                with operation_lock():
                    if choice == '1':
                        start_panel(port)
                    elif choice == '2':
                        update_data(port, end)
                    else:
                        print('面板已关闭。' if stop_panel(port) else '面板未运行。')
            else:
                print('请输入 0、1、2、3 或 4。')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f'操作未完成：{exc}')


def main():
    parser = argparse.ArgumentParser(description='Quant 启动与数据更新菜单')
    parser.add_argument('action', nargs='?', choices=('menu', 'panel', 'update', 'report', 'stop'), default='menu')
    parser.add_argument('--port', type=int, default=8600)
    parser.add_argument('--end', default='')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('端口必须在 1 至 65535 之间')
    try:
        if args.action == 'menu':
            return menu(args.port)
        if args.action == 'report':
            show_report()
            return 0
        with operation_lock():
            if args.action == 'panel':
                start_panel(args.port, not args.no_browser)
            elif args.action == 'update':
                return update_data(args.port, args.end)
            else:
                stop_panel(args.port)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'操作未完成：{exc}', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\n启动器已退出。')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
