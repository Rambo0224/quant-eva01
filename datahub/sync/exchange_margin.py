"""Verified SSE + SZSE totals as a fallback for the three margin indicators."""
from __future__ import annotations
import json
import pandas as pd
import requests

from .storage import start_run, finish_run

SOURCE = 'exchange_margin_total'
FIELDS = {'margin-balance': ('rzye','jrrzye'),
          'margin-purchase': ('rzmre','jrrzmr'),
          'short-balance': ('rqylje','jrrjye')}


def combine_day(shanghai, shenzhen):
    """SSE publishes yuan; SZSE's current metadata explicitly states 亿元."""
    metadata=shenzhen['metadata']
    data=shenzhen.get('data') or []
    if len(data)!=1:
        raise ValueError('SZSE did not return exactly one aggregate row')
    result={}
    for indicator,(sh,sz) in FIELDS.items():
        label=metadata['cols'].get(sz,'')
        scale=1 if '(亿元)' in label else 1e-8 if '(元)' in label else None
        if scale is None:
            raise ValueError(f'Unverified SZSE unit: {label}')
        left=float(shanghai[sh])/1e8
        right=float(str(data[0][sz]).replace(',',''))*scale
        if not (0<=left<float('inf') and 0<=right<float('inf')):
            raise ValueError('Invalid margin aggregate')
        result[indicator]=left+right
    return result


def refresh(end):
    from macro_replay.db import ensure_db, upsert_observations
    from scripts.fetch_etf_share_history import _trade_dates
    conn=ensure_db()
    report={'success':[],'failed':[],'end':end}
    run=start_run(conn,SOURCE,{'end':end})
    try:
        latest=[conn.execute('SELECT max(obs_time)::DATE FROM observations WHERE indicator_id=?',[key]).fetchone()[0] for key in FIELDS]
        start=str(min(d for d in latest if d)) if all(latest) else str((pd.Timestamp(end)-pd.Timedelta(days=45)).date())
        session=requests.Session()
        headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.sse.com.cn/'}
        response=session.get('https://query.sse.com.cn/marketdata/tradedata/queryMargin.do',
            params={'isPagination':'true','beginDate':start.replace('-',''),'endDate':end.replace('-',''),
                    'tabType':'','stockCode':'','pageHelp.pageSize':'5000','pageHelp.pageNo':'1'},headers=headers,timeout=25)
        response.raise_for_status()
        sse=response.json()
        conn.execute("INSERT INTO raw.acquisition_batches(run_id,source_id,symbol,request,records,representation) VALUES (?,?,?,?,?,'http_json')",
                     [run,SOURCE,'SSE',json.dumps({'start':start,'end':end}),json.dumps(sse,ensure_ascii=False)])
        rows={pd.Timestamp(str(x['opDate'])).date().isoformat():x for x in sse.get('result',[])}
        for day in reversed(_trade_dates(start,end)):
            date=day.date().isoformat()
            if date not in rows:
                report['failed'].append({'date':date,'reason':'SSE has not published this date'})
                continue
            try:
                response=session.get('https://www.szse.cn/api/report/ShowReport/data',
                    params={'SHOWTYPE':'JSON','CATALOGID':'1837_xxpl','txtDate':date,'tab1PAGENO':'1'},
                    headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.szse.cn/'},timeout=25)
                response.raise_for_status()
                szse=response.json()[0]
                conn.execute("INSERT INTO raw.acquisition_batches(run_id,source_id,symbol,request,records,representation) VALUES (?,?,?,?,?,'http_json')",
                             [run,SOURCE,'SZSE',json.dumps({'date':date}),json.dumps(szse,ensure_ascii=False)])
                dates=[c.get('defaultValue') for c in szse['metadata'].get('conditions',[]) if c.get('name')=='txtDate']
                if dates != [date]: raise ValueError('SZSE response date differs from request')
                values=combine_day(rows[date],szse)
                for indicator,value in values.items():
                    upsert_observations(conn,indicator,f"{indicator}:{indicator.replace('-','_')}",SOURCE,
                        [(day.to_pydatetime(),value,{'method':'SSE yuan / 1e8 + SZSE reported unit converted to 亿元','run_id':run})],unit='亿元')
                report['success'].append({'date':date,'indicators':list(values)})
            except Exception as exc:
                report['failed'].append({'date':date,'error':str(exc)})
        finish_run(conn,run,error=json.dumps(report['failed'],ensure_ascii=False) if report['failed'] else None)
    except Exception as exc:
        report['failed'].append({'error':str(exc)})
        finish_run(conn,run,error=str(exc))
    finally:
        conn.close()
    return report
