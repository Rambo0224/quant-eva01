from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time
from typing import Any

import pandas as pd
import requests


class EastmoneySourceUnavailable(RuntimeError):
    """Raised when Eastmoney does not return a complete market snapshot."""


@dataclass(frozen=True)
class EastmoneySnapshotResult:
    frame: pd.DataFrame
    source_id: str
    endpoint: str
    fetched_at: datetime
    as_of: pd.Timestamp
    note: str = ""


ENDPOINTS = (
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
)
FIELDS = "f12,f14,f2,f3,f5,f6,f8,f9,f10,f23"
RENAME = {
    "f12": "code",
    "f14": "name",
    "f2": "price",
    "f3": "change_pct",
    "f5": "volume",
    "f6": "amount",
    "f8": "turnover_rate",
    "f9": "pe",
    "f10": "volume_ratio",
    "f23": "pb",
}
NUMERIC = ("price", "change_pct", "volume", "amount", "turnover_rate", "pe", "volume_ratio", "pb")


def _request_page(session: requests.Session, endpoint: str, page: int, page_size: int, timeout: float) -> dict[str, Any]:
    params = {
        "pn": page,
        "pz": page_size,
        "po": 1,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2",
        "fields": FIELDS,
    }
    response = session.get(endpoint, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise EastmoneySourceUnavailable(f"Eastmoney page {page} returned an unexpected payload")
    return payload


def _fetch_page_with_retry(session: requests.Session, page: int, page_size: int, timeout: float, retries: int) -> tuple[str, dict[str, Any]]:
    last_error: Exception | None = None
    for endpoint in ENDPOINTS:
        for attempt in range(max(1, retries)):
            try:
                return endpoint, _request_page(session, endpoint, page, page_size, timeout)
            except Exception as exc:
                last_error = exc
                if attempt + 1 < max(1, retries):
                    time.sleep(0.6 * (attempt + 1))
    raise EastmoneySourceUnavailable(str(last_error or "Eastmoney request failed"))


def _normalize_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows).rename(columns=RENAME)
    for column in RENAME.values():
        if column not in frame.columns:
            frame[column] = pd.NA
    frame["code"] = frame["code"].astype("string").str.strip()
    frame = frame[frame["code"].str.fullmatch(r"\d{6}", na=False)].copy()
    for column in NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.drop_duplicates("code", keep="last").reset_index(drop=True)
    return frame[["code", "name", *NUMERIC]]


def fetch_a_share_snapshot(
    as_of: str | None = None,
    *,
    page_size: int = 100,
    timeout: float = 30.0,
    retries: int = 3,
) -> EastmoneySnapshotResult:
    """Fetch the complete current A-share snapshot and normalize valuation/flow fields."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    session = requests.Session()
    session.trust_env = False
    try:
        endpoint, first = _fetch_page_with_retry(session, 1, page_size, timeout, retries)
        data = first["data"]
        total = int(data.get("total") or 0)
        rows = list(data.get("diff") or [])
        if total <= 0 or not rows:
            raise EastmoneySourceUnavailable("Eastmoney returned an empty A-share snapshot")
        pages = math.ceil(total / page_size)
        for page in range(2, pages + 1):
            page_endpoint, payload = _fetch_page_with_retry(session, page, page_size, timeout, retries)
            if page_endpoint != endpoint:
                endpoint = page_endpoint
            page_rows = list(payload.get("data", {}).get("diff") or [])
            if not page_rows:
                raise EastmoneySourceUnavailable(f"Eastmoney page {page}/{pages} was empty")
            rows.extend(page_rows)
    finally:
        session.close()

    frame = _normalize_rows(rows)
    if len(frame) < total * 0.95:
        raise EastmoneySourceUnavailable(f"Eastmoney snapshot incomplete: {len(frame)} rows for reported total {total}")
    return EastmoneySnapshotResult(
        frame=frame,
        source_id="eastmoney_public",
        endpoint=endpoint,
        fetched_at=datetime.now(timezone.utc),
        as_of=pd.Timestamp(as_of) if as_of else pd.Timestamp.now().normalize(),
        note="Eastmoney public full-A-share quote snapshot; valuation fields use positive finite observations only",
    )
