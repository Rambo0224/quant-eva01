from __future__ import annotations
import json
import os
import subprocess
import sys
import uuid
from .storage import ROOT


def call(module, function, args=(), kwargs=None, timeout=300):
    directory=ROOT/'logs'/'data_sync'/'tasks'
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'{uuid.uuid4()}.json'
    path.write_text(json.dumps(dict(module=module,function=function,args=args,kwargs=kwargs or {})),encoding='utf-8')
    with path.with_suffix('.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-u','-m','datahub.sync.task_worker',str(path)],cwd=ROOT,stdout=log,stderr=log,
                                 env=dict(os.environ,PYTHONIOENCODING='utf-8'))
        try: process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name=='nt': subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else: process.kill()
            process.wait()
            return {'failed':[{'error':f'{module}.{function} exceeded {timeout}s','log':str(path.with_suffix('.log'))}]}
    result_path=path.with_suffix('.result.json')
    if not result_path.exists():
        return {'failed':[{'error':f'{module}.{function} exited without a result','returncode':process.returncode}]}
    return json.loads(result_path.read_text(encoding='utf-8'))
