from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple
import json

import duckdb
import pandas as pd

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "replay.duckdb"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT,
    source_type TEXT,
    description TEXT,
    meta JSON
);

CREATE TABLE IF NOT EXISTS series (
    series_id TEXT,
    indicator_id TEXT,
    source_id TEXT,
    code TEXT,
    unit TEXT,
    frequency TEXT,
    meta JSON
);

CREATE TABLE IF NOT EXISTS raw_payloads (
    id BIGINT,
    source_id TEXT,
    indicator_id TEXT,
    request JSON,
    response JSON,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS observations (
    series_id TEXT,
    indicator_id TEXT,
    source_id TEXT,
    obs_time TIMESTAMP,
    value DOUBLE,
    unit TEXT,
    extra JSON,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS equity_daily_bars (
    source_id TEXT,
    symbol TEXT,
    obs_time TIMESTAMP,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    amount DOUBLE,
    turnover_rate DOUBLE,
    extra JSON,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS etf_share_daily (
    source_id TEXT,
    symbol TEXT,
    obs_time TIMESTAMP,
    name TEXT,
    exchange TEXT,
    category TEXT,
    shares DOUBLE,
    share_change DOUBLE,
    close DOUBLE,
    estimated_flow_amount DOUBLE,
    extra JSON,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS market_daily_bars (
    source_id TEXT,
    asset_id TEXT,
    symbol TEXT,
    obs_time TIMESTAMP,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume DOUBLE,
    open_interest DOUBLE,
    unit TEXT,
    extra JSON,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS charts (
    indicator_id TEXT,
    html_path TEXT,
    image_path TEXT,
    rendered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_summary TEXT
);

CREATE TABLE IF NOT EXISTS manual_import_logs (
    indicator_id TEXT,
    source_id TEXT,
    file_path TEXT,
    sheet_name TEXT,
    file_mtime TIMESTAMP,
    import_mode TEXT,
    rows_read BIGINT,
    rows_written BIGINT,
    date_min TIMESTAMP,
    date_max TIMESTAMP,
    status TEXT,
    note TEXT,
    error_message TEXT,
    meta JSON,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Standard/core layer. Legacy ingestion tables above remain compatible while
-- consumers migrate to these stable entities and fields.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS instrument_master (
    instrument_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    name TEXT,
    asset_type TEXT NOT NULL,
    exchange TEXT,
    currency TEXT DEFAULT 'CNY',
    list_date DATE,
    delist_date DATE,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS instrument_identifiers (
    instrument_id TEXT NOT NULL,
    identifier TEXT NOT NULL,
    identifier_type TEXT NOT NULL,
    source_id TEXT,
    valid_from DATE,
    valid_to DATE,
    meta JSON,
    UNIQUE (instrument_id, identifier, identifier_type, source_id)
);

CREATE TABLE IF NOT EXISTS market_daily_bars_core (
    instrument_id TEXT NOT NULL,
    trade_date DATE NOT NULL,
    frequency TEXT NOT NULL DEFAULT '1d',
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume DOUBLE, amount DOUBLE, turnover_rate DOUBLE,
    open_interest DOUBLE,
    price_adjustment TEXT NOT NULL DEFAULT 'raw',
    currency TEXT DEFAULT 'CNY',
    volume_unit TEXT, amount_unit TEXT,
    source_id TEXT, ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (instrument_id, trade_date, frequency, price_adjustment)
);

CREATE TABLE IF NOT EXISTS etf_daily_facts (
    instrument_id TEXT NOT NULL,
    trade_date DATE NOT NULL,
    shares DOUBLE, share_change DOUBLE, close DOUBLE,
    estimated_flow_amount DOUBLE,
    source_id TEXT, extra JSON, ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (instrument_id, trade_date, source_id)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id UUID DEFAULT uuid() PRIMARY KEY,
    source_id TEXT, started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP, request_range JSON, rows_read BIGINT,
    rows_written BIGINT, date_min DATE, date_max DATE,
    status TEXT, error_message TEXT, meta JSON
);

CREATE TABLE IF NOT EXISTS ingestion_errors (
    run_id UUID, source_id TEXT, occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stage TEXT, error_type TEXT, message TEXT, payload JSON
);

CREATE TABLE IF NOT EXISTS data_quality_results (
    run_id UUID, table_name TEXT, instrument_id TEXT, trade_date DATE,
    check_name TEXT, severity TEXT, issue_count BIGINT DEFAULT 1,
    status TEXT DEFAULT 'open', description TEXT, checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_db() -> duckdb.DuckDBPyConnection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(SCHEMA_SQL)
    conn.execute("ALTER TABLE equity_daily_bars ADD COLUMN IF NOT EXISTS amount DOUBLE")
    conn.execute("ALTER TABLE equity_daily_bars ADD COLUMN IF NOT EXISTS turnover_rate DOUBLE")
    for column, typ in (("provider", "TEXT"), ("endpoint_or_method", "TEXT"), ("license_note", "TEXT"), ("priority", "INTEGER"), ("coverage", "TEXT"), ("enabled", "BOOLEAN")):
        conn.execute(f"ALTER TABLE sources ADD COLUMN IF NOT EXISTS {column} {typ}")
    from datahub.sync.storage import initialize
    initialize(conn)
    return conn


def store_payload(conn: duckdb.DuckDBPyConnection, indicator_id: str, source_id: str, request: Dict[str, Any], response: Dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO raw_payloads (source_id, indicator_id, request, response) VALUES (?, ?, ?, ?)",
        [source_id, indicator_id, request, response],
    )


def _filter_new_rows(
    conn: duckdb.DuckDBPyConnection,
    indicator_id: str,
    series_id: str,
    rows: List[Tuple[str, float, Dict[str, Any]]],
) -> List[Tuple[str, float, Dict[str, Any]]]:
    if not rows:
        return []

    latest_time = latest_observation_time(conn, indicator_id, series_id)
    if latest_time is None:
        return rows

    return [row for row in rows if row[0] > latest_time]


def upsert_observations(conn: duckdb.DuckDBPyConnection, indicator_id: str, series_id: str, source_id: str, rows: List[Tuple[str, float, Dict[str, Any]]], unit: str | None = None) -> None:
    rows_to_insert = _filter_new_rows(conn, indicator_id, series_id, rows)
    if not rows_to_insert:
        return
    payload = [
        (series_id, indicator_id, source_id, row[0], row[1], unit, row[2])
        for row in rows_to_insert
    ]
    conn.executemany(
        "INSERT INTO observations (series_id, indicator_id, source_id, obs_time, value, unit, extra) VALUES (?, ?, ?, ?, ?, ?, ?)",
        payload,
    )


def insert_missing_observations(
    conn: duckdb.DuckDBPyConnection,
    indicator_id: str,
    series_id: str,
    source_id: str,
    rows: List[Tuple[str, float, Dict[str, Any]]],
    unit: str | None = None,
) -> int:
    """Insert only timestamps absent from a series, including historical gaps."""
    if not rows:
        return 0

    existing = {
        row[0]
        for row in conn.execute(
            """
            SELECT obs_time
            FROM observations
            WHERE indicator_id = ? AND series_id = ?
            """,
            [indicator_id, series_id],
        ).fetchall()
    }
    unique_rows = {row[0]: row for row in rows}
    rows_to_insert = [row for timestamp, row in unique_rows.items() if timestamp not in existing]
    if not rows_to_insert:
        return 0
    payload = [
        (series_id, indicator_id, source_id, row[0], row[1], unit, row[2])
        for row in rows_to_insert
    ]
    conn.executemany(
        "INSERT INTO observations (series_id, indicator_id, source_id, obs_time, value, unit, extra) VALUES (?, ?, ?, ?, ?, ?, ?)",
        payload,
    )
    return len(rows_to_insert)


def insert_missing_equity_bars(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    frame,
) -> int:
    """Insert only unseen source/symbol/date OHLCV rows."""
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"equity bar frame missing columns: {sorted(missing)}")
    if frame.empty:
        return 0

    columns = ["symbol", "date", "open", "high", "low", "close", "volume"]
    optional_columns = [column for column in ("amount", "turnover_rate", "extra") if column in frame.columns]
    data = frame[columns + optional_columns].copy()
    if "price_adjustment" in frame:
        data["extra"] = [dict(value if isinstance(value, dict) else {}, price_adjustment={"1":"hfq","2":"qfq","3":"raw","none":"raw"}.get(str(adj),str(adj))) for value, adj in zip(frame.get("extra", pd.Series([{}] * len(frame))), frame["price_adjustment"])]
    data["symbol"] = data["symbol"].astype("string")
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data = data.dropna(subset=["symbol", "date", "open", "high", "low", "close"])
    if data.empty:
        return 0
    data = data.drop_duplicates(["symbol", "date"], keep="last")
    symbols = data["symbol"].dropna().astype(str).unique().tolist()
    placeholders = ", ".join("?" for _ in symbols)
    existing = {
        (row[0], row[1])
        for row in conn.execute(
            f"SELECT regexp_replace(symbol, '\\.(SS|SZ|BJ)$', '') AS symbol, obs_time FROM equity_daily_bars WHERE source_id = ? AND regexp_replace(symbol, '\\.(SS|SZ|BJ)$', '') IN ({placeholders})",
            [source_id, *symbols],
        ).fetchall()
    }
    rows = [
        (
            source_id,
            str(row.symbol),
            row.date.to_pydatetime(),
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
            float(row.volume) if row.volume == row.volume else None,
            float(getattr(row, "amount", float("nan"))) if getattr(row, "amount", float("nan")) == getattr(row, "amount", float("nan")) else None,
            float(getattr(row, "turnover_rate", float("nan"))) if getattr(row, "turnover_rate", float("nan")) == getattr(row, "turnover_rate", float("nan")) else None,
            getattr(row, "extra", {}) if isinstance(getattr(row, "extra", {}), dict) else {},
        )
        for row in data.itertuples(index=False)
        if (str(row.symbol), row.date.to_pydatetime()) not in existing
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO equity_daily_bars
        (source_id, symbol, obs_time, open, high, low, close, volume, amount, turnover_rate, extra)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def insert_missing_equity_bars_bulk(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    frame: pd.DataFrame,
) -> int:
    """Insert a market-wide batch with one DuckDB anti-join.

    The daily-all BaoStock endpoint returns thousands of rows at once. Using
    a registered DataFrame avoids Python-level row-by-row existence checks.
    """
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"equity bar frame missing columns: {sorted(missing)}")
    if frame.empty:
        return 0

    data = frame.copy()
    if "price_adjustment" in frame:
        data["extra"] = [dict(value if isinstance(value, dict) else {}, price_adjustment={"1":"hfq","2":"qfq","3":"raw","none":"raw"}.get(str(adj),str(adj))) for value, adj in zip(frame.get("extra", pd.Series([{}] * len(frame))), frame["price_adjustment"])]
    data["symbol"] = data["symbol"].astype("string")
    data["obs_time"] = pd.to_datetime(data["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        if column not in data.columns:
            data[column] = None
        else:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["symbol", "obs_time", "open", "high", "low", "close"])
    data = data.drop_duplicates(["symbol", "obs_time"], keep="last")
    data["source_id"] = source_id
    data["extra_json"] = data.get("extra", pd.Series(index=data.index, dtype="object")).map(
        lambda value: json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False)
    )
    data = data[
        ["source_id", "symbol", "obs_time", "open", "high", "low", "close", "volume", "amount", "turnover_rate", "extra_json"]
    ]
    conn.register("_incoming_equity_bars", data)
    try:
        inserted_rows = conn.execute(
            """
            INSERT INTO equity_daily_bars
            (source_id, symbol, obs_time, open, high, low, close, volume, amount, turnover_rate, extra)
            SELECT source_id, symbol, obs_time, open, high, low, close, volume, amount,
                   turnover_rate, CAST(extra_json AS JSON)
            FROM _incoming_equity_bars incoming
            WHERE NOT EXISTS (
                SELECT 1
                FROM equity_daily_bars existing
                WHERE existing.source_id = incoming.source_id
                  AND existing.symbol = incoming.symbol
                  AND existing.obs_time = incoming.obs_time
            )
            RETURNING symbol
            """
        ).fetchall()
    finally:
        conn.unregister("_incoming_equity_bars")
    return len(inserted_rows)


def insert_missing_etf_share_rows(
    conn: duckdb.DuckDBPyConnection,
    source_id: str,
    frame: pd.DataFrame,
) -> int:
    """Insert unseen ETF share-count rows keyed by source/symbol/date."""
    required = {"symbol", "date", "shares"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"ETF share frame missing columns: {sorted(missing)}")
    if frame.empty:
        return 0

    data = frame.copy()
    data["source_id"] = source_id
    data["symbol"] = data["symbol"].astype("string").str.zfill(6)
    data["obs_time"] = pd.to_datetime(data["date"], errors="coerce")
    data["shares"] = pd.to_numeric(data["shares"], errors="coerce")
    for column in ("name", "exchange", "category"):
        if column not in data.columns:
            data[column] = None
    for column in ("share_change", "close", "estimated_flow_amount"):
        if column not in data.columns:
            data[column] = None
        else:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["symbol", "obs_time", "shares"]).drop_duplicates(
        ["source_id", "symbol", "obs_time"],
        keep="last",
    )
    if data.empty:
        return 0
    data["extra_json"] = data.get("extra", pd.Series(index=data.index, dtype="object")).map(
        lambda value: json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False)
    )
    data = data[
        [
            "source_id",
            "symbol",
            "obs_time",
            "name",
            "exchange",
            "category",
            "shares",
            "share_change",
            "close",
            "estimated_flow_amount",
            "extra_json",
        ]
    ]
    conn.register("_incoming_etf_share_rows", data)
    try:
        inserted_rows = conn.execute(
            """
            INSERT INTO etf_share_daily
            (source_id, symbol, obs_time, name, exchange, category, shares,
             share_change, close, estimated_flow_amount, extra)
            SELECT source_id, symbol, obs_time, name, exchange, category, shares,
                   share_change, close, estimated_flow_amount, CAST(extra_json AS JSON)
            FROM _incoming_etf_share_rows incoming
            WHERE NOT EXISTS (
                SELECT 1
                FROM etf_share_daily existing
                WHERE existing.source_id = incoming.source_id
                  AND existing.symbol = incoming.symbol
                  AND existing.obs_time = incoming.obs_time
            )
            RETURNING symbol
            """
        ).fetchall()
    finally:
        conn.unregister("_incoming_etf_share_rows")
    return len(inserted_rows)


def latest_observation_time(
    conn: duckdb.DuckDBPyConnection,
    indicator_id: str,
    series_id: str,
):
    row = conn.execute(
        """
        SELECT max(obs_time)
        FROM observations
        WHERE indicator_id = ? AND series_id = ?
        """,
        [indicator_id, series_id],
    ).fetchone()
    return row[0] if row else None


def earliest_observation_time(
    conn: duckdb.DuckDBPyConnection,
    indicator_id: str,
    series_id: str,
):
    row = conn.execute(
        """
        SELECT min(obs_time)
        FROM observations
        WHERE indicator_id = ? AND series_id = ?
        """,
        [indicator_id, series_id],
    ).fetchone()
    return row[0] if row else None


def replace_observations_since(
    conn: duckdb.DuckDBPyConnection,
    indicator_id: str,
    series_id: str,
    source_id: str,
    rows: List[Tuple[str, float, Dict[str, Any]]],
    since,
    unit: str | None = None,
) -> None:
    rows_to_insert = [row for row in rows if row[0] >= since]
    conn.execute(
        """
        DELETE FROM observations
        WHERE indicator_id = ? AND series_id = ? AND obs_time >= ?
        """,
        [indicator_id, series_id, since],
    )
    if not rows_to_insert:
        return
    payload = [
        (series_id, indicator_id, source_id, row[0], row[1], unit, row[2])
        for row in rows_to_insert
    ]
    conn.executemany(
        "INSERT INTO observations (series_id, indicator_id, source_id, obs_time, value, unit, extra) VALUES (?, ?, ?, ?, ?, ?, ?)",
        payload,
    )


def record_chart(conn: duckdb.DuckDBPyConnection, indicator_id: str, html_path: Path, image_path: Path, source_summary: str) -> None:
    conn.execute("DELETE FROM charts WHERE indicator_id = ?", [indicator_id])
    conn.execute(
        "INSERT INTO charts (indicator_id, html_path, image_path, source_summary) VALUES (?, ?, ?, ?)",
        [indicator_id, str(html_path), str(image_path), source_summary],
    )


def record_manual_import(
    conn: duckdb.DuckDBPyConnection,
    indicator_id: str,
    source_id: str,
    file_path: str,
    sheet_name: str,
    file_mtime,
    import_mode: str,
    rows_read: int,
    rows_written: int,
    date_min,
    date_max,
    status: str,
    note: str = "",
    error_message: str = "",
    meta: Dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO manual_import_logs (
            indicator_id, source_id, file_path, sheet_name, file_mtime,
            import_mode, rows_read, rows_written, date_min, date_max,
            status, note, error_message, meta
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            indicator_id,
            source_id,
            file_path,
            sheet_name,
            file_mtime,
            import_mode,
            rows_read,
            rows_written,
            date_min,
            date_max,
            status,
            note,
            error_message,
            meta or {},
        ],
    )
