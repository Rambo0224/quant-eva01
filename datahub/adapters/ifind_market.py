from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import importlib.util
import os
from pathlib import Path
import re
import time
from typing import Any

import pandas as pd
import requests


class IfindMarketSourceUnavailable(RuntimeError):
    """Raised when iFinD does not return a complete downloadable result."""


@dataclass(frozen=True)
class IfindFetchResult:
    frame: pd.DataFrame
    source_id: str
    query: str
    download_urls: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class IfindStatsResult:
    frame: pd.DataFrame
    source_id: str
    query: str
    sample_count: int | None
    note: str = ""


DEFAULT_SKILL_DIR = Path(r"C:\Users\heshu\.codex\skills\ifind-finance-data")
URL_PATTERN = re.compile(r"https?://[^\s`]+?\.csv")


def _load_call_module():
    skill_dir = Path(os.environ.get("IFIND_FINANCE_DATA_SKILL_DIR", DEFAULT_SKILL_DIR))
    module_path = skill_dir / "call.py"
    if not module_path.exists():
        raise IfindMarketSourceUnavailable(f"iFinD call module not found: {module_path}")
    spec = importlib.util.spec_from_file_location("omnisignal_ifind_call", module_path)
    if spec is None or spec.loader is None:
        raise IfindMarketSourceUnavailable(f"Cannot load iFinD call module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _answer_from_response(response: dict[str, Any]) -> str:
    try:
        text = response["data"]["result"]["content"][0]["text"]
        import json

        payload = json.loads(text)
        answer = payload.get("data", {}).get("answer")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise IfindMarketSourceUnavailable(f"Unexpected iFinD response shape: {exc}") from exc
    if not isinstance(answer, str) or not answer.strip() or "工具调用结果为空" in answer:
        raise IfindMarketSourceUnavailable("iFinD returned no usable answer")
    return answer


def _query_answer(date: str, fields: str, tool_name: str, retries: int) -> tuple[str, str]:
    query = f"全部A股 {pd.Timestamp(date).strftime('%Y-%m-%d')} 的{fields}"
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            module = _load_call_module()
            response = module.call("stock", tool_name, {"query": query})
            if not response.get("ok"):
                raise IfindMarketSourceUnavailable(f"iFinD request returned an error: {response}")
            return query, _answer_from_response(response)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, retries):
                time.sleep(1.5 * (attempt + 1))
    raise IfindMarketSourceUnavailable(str(last_error or "iFinD returned no usable answer"))


def extract_download_urls(answer: str) -> tuple[str, ...]:
    urls = []
    for url in URL_PATTERN.findall(answer):
        cleaned = url.rstrip("，。,.;；")
        if cleaned not in urls:
            urls.append(cleaned)
    return tuple(urls)


def _download_csv(url: str, timeout: float) -> pd.DataFrame:
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        if not response.content:
            raise IfindMarketSourceUnavailable(f"Empty iFinD CSV response: {url}")
        return pd.read_csv(BytesIO(response.content))
    except Exception as exc:
        if isinstance(exc, IfindMarketSourceUnavailable):
            raise
        raise IfindMarketSourceUnavailable(f"Cannot download iFinD CSV {url}: {exc}") from exc
    finally:
        session.close()


def fetch_full_a_share_csv(
    date: str,
    fields: str,
    *,
    query_prefix: str = "全部A股",
    timeout: float = 90.0,
    retries: int = 3,
) -> IfindFetchResult:
    """Fetch one complete full-A-share cross-section through iFinD's CSV result."""
    query = f"{query_prefix} {pd.Timestamp(date).strftime('%Y-%m-%d')} 的{fields}"
    last_error: Exception | None = None
    answer = ""
    urls: tuple[str, ...] = ()
    for attempt in range(max(1, retries)):
        try:
            module = _load_call_module()
            response = module.call("stock", "get_stock_performance", {"query": query})
            if not response.get("ok"):
                raise IfindMarketSourceUnavailable(f"iFinD request returned an error: {response}")
            answer = _answer_from_response(response)
            urls = extract_download_urls(answer)
            if urls:
                break
            raise IfindMarketSourceUnavailable("iFinD answer did not contain a downloadable CSV URL")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, retries):
                time.sleep(1.5 * (attempt + 1))
    if not urls:
        raise IfindMarketSourceUnavailable(str(last_error or "iFinD returned no downloadable CSV"))

    frames = [_download_csv(url, timeout) for url in urls]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        raise IfindMarketSourceUnavailable("iFinD downloadable CSVs were all empty")

    # A single query may produce a metric table and a companion metadata table.
    # Keep the table with the requested metric columns and merge compatible tables.
    frame = frames[0].copy()
    for candidate in frames[1:]:
        if "证券代码" in candidate.columns and "证券代码" in frame.columns:
            frame = frame.merge(candidate, on=[column for column in ("证券代码", "日期") if column in frame.columns and column in candidate.columns], how="outer")
    if "证券代码" not in frame.columns:
        raise IfindMarketSourceUnavailable("iFinD CSV has no security-code column")
    if "日期" in frame.columns:
        parsed_dates = pd.to_datetime(frame["日期"].astype("string"), errors="coerce")
        frame = frame.loc[parsed_dates.notna()].copy()
        frame["日期"] = frame["日期"].astype("string").str.replace(r"\.0$", "", regex=True)
    frame = frame.drop_duplicates(subset=[column for column in ("证券代码", "日期") if column in frame.columns]).reset_index(drop=True)
    return IfindFetchResult(
        frame=frame,
        source_id="ifind_mcp",
        query=query,
        download_urls=urls,
        note="iFinD full-A-share CSV result; response text is never used as the data table",
    )


def parse_statistical_summary(answer: str) -> pd.DataFrame:
    """Parse iFinD's exact full-universe statistics table from the answer text."""
    rows: list[dict[str, Any]] = []
    in_table = False
    for line in answer.splitlines():
        text = line.strip()
        if text.startswith("| 日期 | 指标名称 | 均值 | 最大值 | 中位数 | 最小值"):
            in_table = True
            continue
        if not in_table or not text.startswith("|"):
            continue
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        if len(cells) != 6 or cells[0] == "---":
            continue
        values = pd.to_numeric(pd.Series(cells[2:]), errors="coerce")
        if pd.isna(values.iloc[2]):
            continue
        rows.append(
            {
                "date": pd.to_datetime(cells[0], errors="coerce"),
                "metric": cells[1],
                "mean": values.iloc[0],
                "max": values.iloc[1],
                "median": values.iloc[2],
                "min": values.iloc[3],
            }
        )
    return pd.DataFrame(rows, columns=["date", "metric", "mean", "max", "median", "min"])


def fetch_statistical_summary(
    date: str,
    fields: str,
    *,
    tool_name: str = "get_stock_performance",
    retries: int = 3,
) -> IfindStatsResult:
    query, answer = _query_answer(date, fields, tool_name, retries)
    frame = parse_statistical_summary(answer)
    if frame.empty:
        raise IfindMarketSourceUnavailable("iFinD answer did not contain a usable statistics table")
    count_match = re.search(r"为您找到\s*([0-9]+)条数据", answer)
    sample_count = int(count_match.group(1)) if count_match else None
    return IfindStatsResult(
        frame=frame,
        source_id="ifind_mcp",
        query=query,
        sample_count=sample_count,
        note="iFinD statistics table computed over the full A-share result set",
    )


def normalize_date_column(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    date_column = next((column for column in ("日期", "交易日期", "报告期") if column in result.columns), None)
    if date_column:
        result["date"] = pd.to_datetime(result[date_column].astype("string"), errors="coerce")
    return result
