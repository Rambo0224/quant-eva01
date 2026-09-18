# A 股多维度市场监测指标库

本文件由 `A股多维度市场监测指标库_筛选后.xlsx` 生成。Terminal 仅作为只读模板；OmniSignal 将在此基础上接入稳定数据源。

共 76 条指标记录。

| 序号 | 指标 | 因子名 | 类型 | 主题 | 首选来源 | 回退来源 | 状态 |
|---:|---|---|---|---|---|---|---|
| 8 | 全市场成交额 | `turnover_total` | 原始 | market-breadth | akshare_public | manual_excel | fallback_only |
| 9 | 全市场换手率 | `turnover_rate` | 原始 | market-breadth | akshare_public | manual_excel | fallback_only |
| 11 | 融资余额 | `margin_balance` | 原始 | market-breadth | exchange_official | akshare_public | adapter_planned |
| 12 | 融资买入额 | `margin_purchase` | 原始 | market-breadth | exchange_official | akshare_public | adapter_planned |
| 13 | 融券余额 | `short_balance` | 原始 | market-breadth | exchange_official | akshare_public | adapter_planned |
| 14 | 融券余量增速 | `short_increase_rate` | 合成 | market-breadth | derived | - | formula_pending |
| 15 | 上涨家数占比 | `advance_ratio` | 合成 | market-breadth | derived | - | formula_pending |
| 16 | 涨停家数 | `limit_up_count` | 原始 | market-breadth | eastmoney_public | ifind_mcp | adapter_planned |
| 17 | 跌停家数 | `limit_down_count` | 原始 | market-breadth | eastmoney_public | ifind_mcp | adapter_planned |
| 18 | 涨停家数占比 | `limit_up_ratio` | 合成 | market-breadth | derived | - | formula_pending |
| 19 | 跌停家数占比 | `limit_down_ratio` | 合成 | market-breadth | derived | - | formula_pending |
| 20 | 真实炸板率 | `explosive_ratio` | 合成 | market-breadth | derived | - | formula_pending |
| 22 | 沪深300收盘价 | `hs300_close` | 原始 | index-valuation | exchange_or_ifind | akshare_public | adapter_planned |
| 23 | 沪深300市盈率 | `hs300_pe` | 原始 | index-valuation | csindex_official | ifind_mcp | adapter_planned |
| 24 | 沪深300市净率 | `hs300_pb` | 原始 | index-valuation | csindex_official | ifind_mcp | adapter_planned |
| 25 | 上证50收盘价 | `sz50_close` | 原始 | index-valuation | exchange_or_ifind | akshare_public | adapter_planned |
| 26 | 上证50市盈率 | `sz50_pe` | 原始 | index-valuation | csindex_official | ifind_mcp | adapter_planned |
| 27 | 上证50市净率 | `sz50_pb` | 原始 | index-valuation | csindex_official | ifind_mcp | adapter_planned |
| 28 | 创业板50收盘价 | `cyb50_close` | 原始 | index-valuation | exchange_or_ifind | akshare_public | adapter_planned |
| 29 | 创业板50市盈率 | `cyb50_pe` | 原始 | index-valuation | csindex_official | ifind_mcp | adapter_planned |
| 30 | 创业板50市净率 | `cyb50_pb` | 原始 | index-valuation | csindex_official | ifind_mcp | adapter_planned |
| 31 | 科创50收盘价 | `kc50_close` | 原始 | index-valuation | exchange_or_ifind | akshare_public | adapter_planned |
| 32 | 科创50市盈率 | `kc50_pe` | 原始 | index-valuation | csindex_official | ifind_mcp | adapter_planned |
| 33 | 科创50市净率 | `kc50_pb` | 原始 | index-valuation | csindex_official | ifind_mcp | adapter_planned |
| 34 | 中证500收盘价 | `zz500_close` | 原始 | index-valuation | exchange_or_ifind | akshare_public | adapter_planned |
| 37 | 沪深300 20日波动率 | `hs300_vol_20d` | 合成 | index-valuation | derived | - | formula_pending |
| 38 | 上证50 20日波动率 | `sz50_vol_20d` | 合成 | index-valuation | derived | - | formula_pending |
| 39 | 创业板50 20日波动率 | `cyb50_vol_20d` | 合成 | index-valuation | derived | - | formula_pending |
| 40 | 科创50 20日波动率 | `kc50_vol_20d` | 合成 | index-valuation | derived | - | formula_pending |
| 41 | IF主力连续波动率 | `if_vol_20d` | 合成 | futures-options | derived | - | formula_pending |
| 42 | IC主力连续波动率 | `ic_vol_20d` | 合成 | futures-options | derived | - | formula_pending |
| 46 | IF基差 | `if_basis` | 合成 | futures-options | derived | - | formula_pending |
| 47 | IC基差 | `ic_basis` | 合成 | futures-options | derived | - | formula_pending |
| 48 | IF年化基差率 | `if_basis_annual` | 合成 | futures-options | derived | - | formula_pending |
| 49 | IC年化基差率 | `ic_basis_annual` | 合成 | futures-options | derived | - | formula_pending |
| 50 | 认沽认购比(成交量) | `pcr_volume` | 原始 | futures-options | ifind_mcp | manual_excel | requires_credentials |
| 51 | 认沽认购比(持仓量) | `pcr_oi` | 原始 | futures-options | ifind_mcp | manual_excel | requires_credentials |
| 52 | 上证50ETF隐含波动率 | `ivix_50` | 原始 | futures-options | ifind_mcp | manual_excel | requires_credentials |
| 53 | QVIX恐慌指数 | `qvix` | 原始 | futures-options | ifind_mcp | manual_excel | requires_credentials |
| 80 | 20日动量中位数 | `momentum_20d_median` | 合成 | cross-sectional | derived | - | formula_pending |
| 81 | 60日动量中位数 | `momentum_60d_median` | 合成 | cross-sectional | derived | - | formula_pending |
| 82 | 5日反转中位数 | `reversal_5d_median` | 合成 | cross-sectional | derived | - | formula_pending |
| 83 | 个股换手率中位数 | `turnover_individual_median` | 合成 | cross-sectional | derived | - | formula_pending |
| 84 | 20日均量比中位数 | `volume_ratio_20d_median` | 合成 | cross-sectional | derived | - | formula_pending |
| 85 | 20日乖离率中位数 | `bias_20d_median` | 合成 | cross-sectional | derived | - | formula_pending |
| 86 | 14日ATR中位数 | `atr_14d_median` | 合成 | cross-sectional | derived | - | formula_pending |
| 87 | 14日RSI中位数 | `rsi_14d_median` | 合成 | cross-sectional | derived | - | formula_pending |
| 88 | 上涨家数占比(日频) | `advance_ratio` | 合成 | market-breadth | derived | - | formula_pending |
| 89 | 上涨家数占比(20日) | `advance_ratio_20d` | 合成 | cross-sectional | derived | - | formula_pending |
| 90 | 60日新高占比 | `high_60d_ratio` | 合成 | cross-sectional | derived | - | formula_pending |
| 91 | 120日新高占比 | `high_120d_ratio` | 合成 | cross-sectional | derived | - | formula_pending |
| 92 | 250日新高占比 | `high_250d_ratio` | 合成 | cross-sectional | derived | - | formula_pending |
| 93 | 站上20日均线占比 | `above_ma20_ratio` | 合成 | cross-sectional | derived | - | formula_pending |
| 94 | 站上60日均线占比 | `above_ma60_ratio` | 合成 | cross-sectional | derived | - | formula_pending |
| 95 | 站上120日均线占比 | `above_ma120_ratio` | 合成 | cross-sectional | derived | - | formula_pending |

## 数据源原则

- iFinD MCP：优先用于生产级历史序列、期货期权和横截面数据，但不复制 Terminal 的令牌。
- 交易所/中证指数官方数据：优先用于融资融券、指数价格和指数估值。
- Eastmoney/AkShare：只作为可审计的公共回退，必须保留缓存、抓取时间和异常状态。
- 合成指标：只在原始序列质量通过检查后计算，数据缺失时留空并告警。
