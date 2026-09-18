from __future__ import annotations

from .config import Indicator, Theme


ETF_THEME = Theme(
    key="etf-market",
    title="ETF市场",
    description="主流宽基ETF与行业ETF的日线收盘价、成交额与份额变化数据。",
)


ETF_WATCHLIST: list[dict[str, str]] = [
    {"code": "510050", "title": "上证50ETF", "category": "broad"},
    {"code": "510300", "title": "沪深300ETF", "category": "broad"},
    {"code": "159919", "title": "沪深300ETF(深市)", "category": "broad"},
    {"code": "510500", "title": "中证500ETF", "category": "broad"},
    {"code": "512100", "title": "中证1000ETF", "category": "broad"},
    {"code": "588000", "title": "科创50ETF", "category": "broad"},
    {"code": "159915", "title": "创业板ETF", "category": "broad"},
    {"code": "510880", "title": "红利ETF", "category": "broad"},
    {"code": "512880", "title": "证券ETF", "category": "broad"},
    {"code": "512010", "title": "医药ETF", "category": "broad"},
    {"code": "512800", "title": "银行ETF", "category": "industry"},
    {"code": "512070", "title": "非银ETF", "category": "industry"},
    {"code": "512480", "title": "半导体ETF", "category": "industry"},
    {"code": "561980", "title": "半导体设备ETF", "category": "industry"},
    {"code": "159995", "title": "芯片ETF", "category": "industry"},
    {"code": "588200", "title": "科创芯片ETF", "category": "industry"},
    {"code": "512690", "title": "酒ETF", "category": "industry"},
    {"code": "515170", "title": "食品饮料ETF", "category": "industry"},
    {"code": "512720", "title": "计算机ETF", "category": "industry"},
    {"code": "512980", "title": "传媒ETF", "category": "industry"},
    {"code": "515030", "title": "新能源车ETF", "category": "industry"},
    {"code": "515790", "title": "光伏ETF", "category": "industry"},
    {"code": "512660", "title": "军工ETF", "category": "industry"},
    {"code": "512400", "title": "有色金属ETF", "category": "industry"},
    {"code": "515220", "title": "煤炭ETF", "category": "industry"},
    {"code": "515210", "title": "钢铁ETF", "category": "industry"},
    {"code": "516020", "title": "化工ETF", "category": "industry"},
    {"code": "512200", "title": "房地产ETF", "category": "industry"},
    {"code": "512170", "title": "医疗ETF", "category": "industry"},
    {"code": "516810", "title": "农业ETF", "category": "industry"},
    {"code": "515880", "title": "通信ETF", "category": "industry"},
]


def build_etf_indicator(entry: dict[str, str], index: int) -> Indicator:
    code = entry["code"]
    title = entry["title"]
    factor = f"etf_{code}_close"
    return Indicator(
        id=f"etf-{code}",
        title=title,
        description=f"ETF观察清单成分，使用公开历史行情接口获取 {code} 的日线数据。",
        server="",
        tool="fund_etf_hist_em",
        arguments={
            "symbol": code,
            "start_date": "2010-01-01",
        },
        chart={
            "type": "line",
            "x_field": "date",
            "y_field": factor,
            "title": title,
            "unit": "元",
            "y_label": "元",
            "source_label": "akshare_public",
        },
        theme="etf-market",
        source="akshare_public",
        series=None,
        calculation=None,
        metadata={
            "excel_sequence": 1000 + index,
            "factor_name": factor,
            "indicator_type": "原始",
            "excel_method": "akshare.fund_etf_hist_em()",
            "preferred_source": "akshare_public",
            "fallback_source": None,
            "source_status": "validated_historical",
            "source_reason": "东方财富 ETF 历史行情接口可返回 ETF 上市以来的日线数据。",
            "dependencies": [],
            "category": entry.get("category", ""),
            "etf_code": code,
        },
    )


def build_etf_indicators() -> list[Indicator]:
    return [build_etf_indicator(entry, index) for index, entry in enumerate(ETF_WATCHLIST, start=1)]


def build_etf_theme() -> Theme:
    return ETF_THEME
