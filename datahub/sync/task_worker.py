"""Isolate a trusted acquisition callable and serialize its result separately."""
import importlib
import json
import sys
from pathlib import Path

if __name__=='__main__':
    path=Path(sys.argv[1])
    request=json.loads(path.read_text(encoding='utf-8'))
    try:
        function=getattr(importlib.import_module(request['module']),request['function'])
        result=function(*request.get('args',[]),**request.get('kwargs',{}))
    except Exception as exc:
        result={'failed':[{'error':str(exc)}]}
    path.with_suffix('.result.json').write_text(json.dumps(result,ensure_ascii=False,default=str),encoding='utf-8')
