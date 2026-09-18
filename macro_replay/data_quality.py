from __future__ import annotations

from typing import Any


PROXY_MARKERS = (
    "proxy",
    "approximately",
    "period proxy",
    "近似",
    "代理",
)
BLOCKED_QUALITY = {"proxy", "synthetic", "unverified"}


def _contains_proxy_marker(value: Any) -> bool:
    text = str(value or "").lower()
    return any(marker in text for marker in PROXY_MARKERS)


def chart_data_quality(indicator, raw_df=None, plot_df=None) -> tuple[bool, str]:
    """Return whether a chart is backed by real, non-proxy observations."""
    metadata = getattr(indicator, "metadata", {}) or {}
    quality = str(metadata.get("data_quality", "real")).lower()
    if quality in BLOCKED_QUALITY:
        return False, metadata.get(
            "quality_reason",
            "当前数据被标记为代理或未验证数据，暂不生成图表。",
        )

    if raw_df is not None and not raw_df.empty and "extra" in raw_df.columns:
        for extra in raw_df["extra"].tolist():
            if isinstance(extra, dict):
                values = list(extra.values())
            else:
                values = [extra]
            if any(_contains_proxy_marker(value) for value in values):
                return False, "当前数据包含期间近似或代理映射，暂不生成图表。"

    if plot_df is not None and plot_df.empty:
        return False, "当前没有可验证的真实数据，暂不生成图表。"

    return True, ""
