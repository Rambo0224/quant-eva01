from __future__ import annotations

from io import StringIO

import pandas as pd
import requests


def fetch_series(url: str, code: str, start_date: str) -> pd.DataFrame:
    try:
        resp = requests.get(url, timeout=30)
    except requests.exceptions.SSLError:
        resp = requests.get(url, timeout=30, verify=False)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    df.columns = [col.strip().lower() for col in df.columns]
    date_col = "date" if "date" in df.columns else "observation_date"
    value_col = "value" if "value" in df.columns else code.lower()
    df = df[[date_col, value_col]]
    df.columns = ["date", code]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df[code] = pd.to_numeric(df[code], errors="coerce")
    df = df[df["date"] >= pd.Timestamp(start_date)]
    df = df.dropna()
    return df
