from __future__ import annotations

from pathlib import Path

import yaml


CONFIG_PATH = Path("config") / "indicators.yaml"

THEME_LABELS = {
    "market-breadth": ("市场交易与情绪", "全市场成交、融资融券、涨跌停和市场宽度指标"),
    "index-valuation": ("指数与估值", "主要宽基指数价格、估值和波动率指标"),
    "futures-options": ("股指期货与期权", "股指期货基差、年化基差、PCR 和隐含波动率"),
    "cross-sectional": ("全市场横截面", "全 A 股票动量、技术面和横截面分布"),
}

INDICATOR_TITLES = {
    "turnover-total": "全市场成交额",
    "turnover-rate": "全市场换手率",
    "margin-balance": "融资余额",
    "margin-purchase": "融资买入额",
    "short-balance": "融券余额",
    "short-increase-rate": "融券余量增速",
    "advance-ratio": "上涨家数占比",
    "limit-up-count": "涨停家数",
    "limit-down-count": "跌停家数",
    "limit-up-ratio": "涨停家数占比",
    "limit-down-ratio": "跌停家数占比",
    "explosive-ratio": "真实炸板率",
    "advance-ratio-2": "上涨家数占比（日频）",
    "advance-decline-line": "腾落指数",
    "hs300-close": "沪深300收盘价",
    "hs300-pe": "沪深300市盈率",
    "hs300-pb": "沪深300市净率",
    "sz50-close": "上证50收盘价",
    "sz50-pe": "上证50市盈率",
    "sz50-pb": "上证50市净率",
    "cyb50-close": "创业板50收盘价",
    "cyb50-pe": "创业板50市盈率",
    "cyb50-pb": "创业板50市净率",
    "kc50-close": "科创50收盘价",
    "kc50-pe": "科创50市盈率",
    "kc50-pb": "科创50市净率",
    "zz500-close": "中证500收盘价",
    "hs300-vol-20d": "沪深300 20日波动率",
    "sz50-vol-20d": "上证50 20日波动率",
    "cyb50-vol-20d": "创业板50 20日波动率",
    "kc50-vol-20d": "科创50 20日波动率",
    "if-vol-20d": "IF主力连续波动率",
    "ic-vol-20d": "IC主力连续波动率",
    "if-basis": "IF基差",
    "ic-basis": "IC基差",
    "if-basis-annual": "IF年化基差率",
    "ic-basis-annual": "IC年化基差率",
    "pcr-volume": "认沽认购比（成交量）",
    "pcr-oi": "认沽认购比（持仓量）",
    "ivix-50": "上证50ETF隐含波动率",
    "qvix": "QVIX恐慌指数",
    "momentum-20d-median": "20日动量中位数",
    "momentum-60d-median": "60日动量中位数",
    "reversal-5d-median": "5日反转中位数",
    "turnover-individual-median": "个股换手率中位数",
    "volume-ratio-20d-median": "20日均量比中位数",
    "bias-20d-median": "20日乖离率中位数",
    "atr-14d-median": "14日ATR中位数",
    "rsi-14d-median": "14日RSI中位数",
    "advance-ratio-20d": "上涨家数占比（20日）",
    "high-60d-ratio": "60日新高占比",
    "high-120d-ratio": "120日新高占比",
    "high-250d-ratio": "250日新高占比",
    "above-ma20-ratio": "站上20日均线占比",
    "above-ma60-ratio": "站上60日均线占比",
    "above-ma120-ratio": "站上120日均线占比",
}

UNIT_LABELS = {
    "turnover_total": ("亿元", "亿元"),
    "turnover_rate": ("%", "%"),
    "margin_balance": ("亿元", "亿元"),
    "margin_purchase": ("亿元", "亿元"),
    "short_balance": ("亿元", "亿元"),
    "short_increase_rate": ("%", "%"),
    "advance_ratio": ("比例", "比例"),
    "limit_up_count": ("家数", "家数"),
    "limit_down_count": ("家数", "家数"),
    "limit_up_ratio": ("比例", "比例"),
    "limit_down_ratio": ("比例", "比例"),
    "explosive_ratio": ("比例", "比例"),
    "advance_decline_line": ("累计家数差", "累计家数差"),
    "hs300_close": ("", "指数点位"),
    "sz50_close": ("", "指数点位"),
    "cyb50_close": ("", "指数点位"),
    "kc50_close": ("", "指数点位"),
    "zz500_close": ("", "指数点位"),
    "hs300_pe": ("倍数", "估值倍数"),
    "hs300_pb": ("倍数", "估值倍数"),
    "sz50_pe": ("倍数", "估值倍数"),
    "sz50_pb": ("倍数", "估值倍数"),
    "cyb50_pe": ("倍数", "估值倍数"),
    "cyb50_pb": ("倍数", "估值倍数"),
    "kc50_pe": ("倍数", "估值倍数"),
    "kc50_pb": ("倍数", "估值倍数"),
    "if_basis": ("", "点位"),
    "ic_basis": ("", "点位"),
    "if_basis_annual": ("%", "%"),
    "ic_basis_annual": ("%", "%"),
    "reversal_5d_median": ("%", "%"),
    "turnover_individual_median": ("%", "%"),
    "bias_20d_median": ("%", "%"),
    "high_60d_ratio": ("比例", "比例"),
    "high_120d_ratio": ("比例", "比例"),
    "high_250d_ratio": ("比例", "比例"),
    "above_ma20_ratio": ("比例", "比例"),
    "above_ma60_ratio": ("比例", "比例"),
    "above_ma120_ratio": ("比例", "比例"),
}

RATIO_LABEL_FACTORS = {
    "pcr_volume",
    "pcr_oi",
    "ivix_50",
    "qvix",
    "if_vol_20d",
    "ic_vol_20d",
    "hs300_vol_20d",
    "sz50_vol_20d",
    "cyb50_vol_20d",
    "kc50_vol_20d",
    "rsi_14d_median",
}


def repair() -> None:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    for theme_key, theme in (raw.get("themes") or {}).items():
        if theme_key in THEME_LABELS:
            theme["title"], theme["description"] = THEME_LABELS[theme_key]
        for indicator in theme.get("indicators", []):
            indicator_id = indicator.get("id")
            title = INDICATOR_TITLES.get(indicator_id)
            if not title:
                continue
            metadata = indicator.get("metadata") or {}
            factor = metadata.get("factor_name", "")
            indicator["title"] = title
            indicator["description"] = ""
            chart = indicator.get("chart") or {}
            chart["title"] = title
            if factor in UNIT_LABELS:
                chart["unit"], chart["y_label"] = UNIT_LABELS[factor]
            elif factor in RATIO_LABEL_FACTORS:
                chart["unit"], chart["y_label"] = "指数/比率", "指数/比率"
            elif "median" in factor or factor.endswith("_20d"):
                chart["unit"], chart["y_label"] = "", "数值"
            indicator["chart"] = chart
            if metadata.get("indicator_type") in {"鍘熷", "原始"}:
                metadata["indicator_type"] = "原始"
            elif metadata.get("indicator_type") in {"鍚堟垚", "合成"}:
                metadata["indicator_type"] = "合成"
            indicator["metadata"] = metadata
    CONFIG_PATH.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )


if __name__ == "__main__":
    repair()
