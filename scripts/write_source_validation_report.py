from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


VALIDATED = {
    "hs300_close", "sz50_close", "cyb50_close", "kc50_close", "zz500_close",
    "hs300_vol_20d", "sz50_vol_20d", "cyb50_vol_20d", "kc50_vol_20d",
}
VALIDATED_HISTORICAL = {
    "turnover_total",
    "margin_balance",
    "margin_purchase",
    "short_balance",
}
DERIVED_HISTORICAL = {"short_increase_rate"}
BAOSTOCK_MARKET_BREADTH = {
    "turnover_rate",
    "advance_ratio",
    "limit_up_count",
    "limit_down_count",
    "limit_up_ratio",
    "limit_down_ratio",
    "explosive_ratio",
}
PROXY_ONLY = {"momentum_20d_median", "momentum_60d_median", "reversal_5d_median", "advance_ratio_20d"}
VALIDATED_RECENT = {"limit_up_count", "limit_down_count", "explosive_ratio"}
IFIND_VALIDATED_RECENT = {
    "turnover_individual_median",
    "limit_up_ratio", "limit_down_ratio",
}
RECENT_ONLY = {"advance_ratio", "advance_ratio_2", "limit_up_count", "limit_down_count", "limit_up_ratio", "limit_down_ratio"}
COMEIN_VALIDATED_RECENT = {"hs300_pe", "hs300_pb", "sz50_pe", "sz50_pb", "cyb50_pe", "cyb50_pb", "kc50_pe", "kc50_pb"}
COMEIN_FUTURES_VALIDATED = {"if_close", "ic_close", "ih_close"}
COMEIN_DERIVED_VALIDATED = {"if_vol_20d", "ic_vol_20d", "if_basis", "ic_basis"}
CFFEX_DERIVED_VALIDATED = {"if_basis_annual", "ic_basis_annual"}
CFFEX_VALIDATED_HISTORICAL = {"if_close", "ic_close", "ih_close"}
AKSHARE_VALIDATED_HISTORICAL = {"pcr_volume", "pcr_oi", "ivix_50", "qvix"}
COMEIN_CROSS_SECTION_VALIDATED = {
    "turnover_total", "turnover_individual_median", "reversal_5d_median",
    "momentum_20d_median", "momentum_60d_median", "advance_ratio_20d",
}
PARTIAL_MARGIN = {"margin_balance", "margin_purchase", "short_balance"}
VALUATION_PE = {"hs300_pe", "sz50_pe", "cyb50_pe", "kc50_pe"}
VALUATION_PB = {"hs300_pb", "sz50_pb", "cyb50_pb", "kc50_pb"}
MARKET_FLOW = {"turnover_total", "turnover_rate"}
BREADTH = {"advance_ratio", "advance_ratio_2"}
LIMIT_RATIOS = {"limit_up_ratio", "limit_down_ratio"}
VENDOR = {"if_close", "ic_close", "ih_close", "pcr_volume", "pcr_oi", "ivix_50", "qvix"}
CFFEX_ADAPTER_READY = {"if_close", "ic_close", "ih_close"}
CROSS_SECTION = {
    "momentum_20d_median", "momentum_60d_median", "reversal_5d_median",
    "turnover_individual_median", "volume_ratio_20d_median", "bias_20d_median", "atr_14d_median",
    "rsi_14d_median", "advance_ratio_20d", "high_60d_ratio", "high_120d_ratio", "high_250d_ratio",
    "above_ma20_ratio", "above_ma60_ratio", "above_ma120_ratio",
}


def classify(factor: str, indicator_type: str) -> tuple[str, str]:
    if factor in PROXY_ONLY:
        return "proxy_only", "The available source maps a different period to this indicator; no chart is generated from the proxy"
    if factor in VALIDATED_HISTORICAL:
        if factor == "turnover_total":
            return "validated_historical", "iFinD EDB indicator S024714285 returned dated observations covering the available historical window"
        return "validated_historical", "iFinD EDB returned a dated daily historical series covering 2010-03-31 onward"
    if factor in DERIVED_HISTORICAL:
        return "validated_historical", "Calculated from the validated iFinD EDB daily short-balance series; the first observation is omitted because it has no prior day"
    if factor in BAOSTOCK_MARKET_BREADTH:
        if factor == "turnover_rate":
            return "validated_historical", "Calculated from BaoStock full-A-share daily amount and individual turnover-rate history since 2021-01-04"
        return "validated_historical", "Calculated from BaoStock full-A-share daily OHLC history since 2021-01-04"
    if factor in RECENT_ONLY:
        return "recent_only", "The available source is limited to recent snapshots or a short public history; no validated one-year series is available"
    if factor in COMEIN_CROSS_SECTION_VALIDATED:
        return "validated_recent", "Comein Finance MCP screener returned the complete A-share cross-section; distributions use positive finite values and documented period proxies"
    if factor in IFIND_VALIDATED_RECENT:
        return "validated_recent", "iFinD full-A-share result has been downloaded and field-validated; current refresh stores the latest verified date or report period"
    if factor in COMEIN_VALIDATED_RECENT:
        return "validated_recent", "Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date"
    if factor in AKSHARE_VALIDATED_HISTORICAL:
        return "validated_historical", "AkShare official public interface returned verified dated observations covering the available historical window"
    if factor in CFFEX_VALIDATED_HISTORICAL:
        return "validated_historical", "AkShare official CFFEX daily interface returned verified IF/IC/IH dominant-contract observations from 2025-01-02 onward"
    if factor in CFFEX_DERIVED_VALIDATED:
        return "validated_historical", "Calculated from stored IF/IC futures closes, aligned spot-index closes, and the CFFEX stock-index-futures expiry schedule"
    if factor in COMEIN_FUTURES_VALIDATED:
        return "validated_recent", "Comein Finance MCP returned 100 dated daily futures bars for the IF/IC/IH dominant series"
    if factor in COMEIN_DERIVED_VALIDATED:
        return "validated_recent", "Calculated from the Comein Finance MCP daily futures bars and aligned benchmark index observations"
    if factor in VALIDATED:
        return "validated", "AkShare 指数日线字段完整，已落库并生成图表"
    if factor in VALIDATED_RECENT:
        return "validated_recent", "AkShare 替代接口已验证；当前版本只能回看最近约 30 个交易日"
    if factor in PARTIAL_MARGIN:
        return "partial", "上交所历史口径已取得；深交所历史接口尚未闭合，不能冒充全市场合计"
    if factor in VALUATION_PE:
        return "partial", "中证估值接口返回市盈率1/市盈率2，具体口径尚未确认"
    if factor in VALUATION_PB:
        return "blocked", "当前 AkShare 中证估值返回字段不含 PB，暂不绘图"
    if factor in MARKET_FLOW:
        return "blocked", "stock_market_fund_flow 与 spot 接口当前连接被远端关闭"
    if factor in BREADTH:
        return "blocked", "全市场快照接口当前连接被远端关闭，无法可靠统计涨跌家数"
    if factor in LIMIT_RATIOS:
        return "pending_dependency", "缺少可验证的总交易家数"
    if factor in CFFEX_ADAPTER_READY:
        return "adapter_ready", "CFFEX official daily adapter is implemented; the current runtime did not return an ingestible response, so no chart is generated"
    if factor in IFIND_VALIDATED_RECENT:
        return "validated_recent", "iFinD full-A-share result has been downloaded and field-validated; current refresh stores the latest verified date or report period"
    if factor in VENDOR:
        return "vendor_pending", "Excel 使用 ifind.csdc；当前 iFinD MCP 工具列表没有对应直接工具"
    if factor in CROSS_SECTION:
        return "blocked", "stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据"
    if indicator_type == "合成":
        return "pending_dependency", "原始依赖尚未全部通过字段和口径校验"
    return "pending_source", "尚未找到可重复验证的稳定来源"


def main() -> None:
    catalog = json.loads(Path("config/indicators.yaml").read_text(encoding="utf-8"))
    rows = []
    for theme_key, theme in catalog["themes"].items():
        for indicator in theme["indicators"]:
            metadata = indicator.get("metadata", {})
            factor = metadata.get("factor_name", "")
            status, reason = classify(factor, metadata.get("indicator_type", ""))
            rows.append({
                "indicator_id": indicator["id"],
                "factor": factor,
                "title": indicator["title"],
                "theme": theme_key,
                "status": status,
                "reason": reason,
                "preferred_source": metadata.get("preferred_source"),
                "fallback_source": metadata.get("fallback_source"),
            })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {},
        "indicators": rows,
    }
    for row in rows:
        report["counts"][row["status"]] = report["counts"].get(row["status"], 0) + 1
    Path("logs").mkdir(exist_ok=True)
    Path("logs/source_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown = [
        "# A 股指标来源验证报告",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "| 状态 | 条数 |",
        "|---|---:|",
        *[f"| {key} | {value} |" for key, value in sorted(report["counts"].items())],
        "",
        "| 因子 | 指标 | 状态 | 说明 |",
        "|---|---|---|---|",
        *[f"| `{row['factor']}` | {row['title']} | {row['status']} | {row['reason']} |" for row in rows],
    ]
    Path("docs/source_validation_report.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
