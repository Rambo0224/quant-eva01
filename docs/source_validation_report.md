# A 股指标来源验证报告

生成时间：2026-07-20T03:34:40.579411+00:00

| 状态 | 条数 |
|---|---:|
| blocked | 10 |
| proxy_only | 4 |
| validated | 9 |
| validated_historical | 19 |
| validated_recent | 13 |

| 因子 | 指标 | 状态 | 说明 |
|---|---|---|---|
| `turnover_total` | 全市场成交额 | validated_historical | iFinD EDB indicator S024714285 returned dated observations covering the available historical window |
| `turnover_rate` | 全市场换手率 | validated_historical | Calculated from BaoStock full-A-share daily amount and individual turnover-rate history since 2021-01-04 |
| `margin_balance` | 融资余额 | validated_historical | iFinD EDB returned a dated daily historical series covering 2010-03-31 onward |
| `margin_purchase` | 融资买入额 | validated_historical | iFinD EDB returned a dated daily historical series covering 2010-03-31 onward |
| `short_balance` | 融券余额 | validated_historical | iFinD EDB returned a dated daily historical series covering 2010-03-31 onward |
| `short_increase_rate` | 融券余量增速 | validated_historical | Calculated from the validated iFinD EDB daily short-balance series; the first observation is omitted because it has no prior day |
| `advance_ratio` | 上涨家数占比 | validated_historical | Calculated from BaoStock full-A-share daily OHLC history since 2021-01-04 |
| `limit_up_count` | 涨停家数 | validated_historical | Calculated from BaoStock full-A-share daily OHLC history since 2021-01-04 |
| `limit_down_count` | 跌停家数 | validated_historical | Calculated from BaoStock full-A-share daily OHLC history since 2021-01-04 |
| `limit_up_ratio` | 涨停家数占比 | validated_historical | Calculated from BaoStock full-A-share daily OHLC history since 2021-01-04 |
| `limit_down_ratio` | 跌停家数占比 | validated_historical | Calculated from BaoStock full-A-share daily OHLC history since 2021-01-04 |
| `explosive_ratio` | 真实炸板率 | validated_historical | Calculated from BaoStock full-A-share daily OHLC history since 2021-01-04 |
| `advance_ratio` | 上涨家数占比(日频) | validated_historical | Calculated from BaoStock full-A-share daily OHLC history since 2021-01-04 |
| `hs300_close` | 沪深300收盘价 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `hs300_pe` | 沪深300市盈率 | validated_recent | Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date |
| `hs300_pb` | 沪深300市净率 | validated_recent | Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date |
| `sz50_close` | 上证50收盘价 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `sz50_pe` | 上证50市盈率 | validated_recent | Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date |
| `sz50_pb` | 上证50市净率 | validated_recent | Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date |
| `cyb50_close` | 创业板50收盘价 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `cyb50_pe` | 创业板50市盈率 | validated_recent | Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date |
| `cyb50_pb` | 创业板50市净率 | validated_recent | Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date |
| `kc50_close` | 科创50收盘价 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `kc50_pe` | 科创50市盈率 | validated_recent | Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date |
| `kc50_pb` | 科创50市净率 | validated_recent | Comein Finance MCP returned a structured index valuation snapshot with an explicit valuation date |
| `zz500_close` | 中证500收盘价 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `hs300_vol_20d` | 沪深300 20日波动率 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `sz50_vol_20d` | 上证50 20日波动率 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `cyb50_vol_20d` | 创业板50 20日波动率 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `kc50_vol_20d` | 科创50 20日波动率 | validated | AkShare 指数日线字段完整，已落库并生成图表 |
| `if_vol_20d` | IF主力连续波动率 | validated_recent | Calculated from the Comein Finance MCP daily futures bars and aligned benchmark index observations |
| `ic_vol_20d` | IC主力连续波动率 | validated_recent | Calculated from the Comein Finance MCP daily futures bars and aligned benchmark index observations |
| `if_basis` | IF基差 | validated_recent | Calculated from the Comein Finance MCP daily futures bars and aligned benchmark index observations |
| `ic_basis` | IC基差 | validated_recent | Calculated from the Comein Finance MCP daily futures bars and aligned benchmark index observations |
| `if_basis_annual` | IF年化基差率 | validated_historical | Calculated from stored IF/IC futures closes, aligned spot-index closes, and the CFFEX stock-index-futures expiry schedule |
| `ic_basis_annual` | IC年化基差率 | validated_historical | Calculated from stored IF/IC futures closes, aligned spot-index closes, and the CFFEX stock-index-futures expiry schedule |
| `pcr_volume` | 认沽认购比(成交量) | validated_historical | AkShare official public interface returned verified dated observations covering the available historical window |
| `pcr_oi` | 认沽认购比(持仓量) | validated_historical | AkShare official public interface returned verified dated observations covering the available historical window |
| `ivix_50` | 上证50ETF隐含波动率 | validated_historical | AkShare official public interface returned verified dated observations covering the available historical window |
| `qvix` | QVIX恐慌指数 | validated_historical | AkShare official public interface returned verified dated observations covering the available historical window |
| `momentum_20d_median` | 20日动量中位数 | proxy_only | The available source maps a different period to this indicator; no chart is generated from the proxy |
| `momentum_60d_median` | 60日动量中位数 | proxy_only | The available source maps a different period to this indicator; no chart is generated from the proxy |
| `reversal_5d_median` | 5日反转中位数 | proxy_only | The available source maps a different period to this indicator; no chart is generated from the proxy |
| `turnover_individual_median` | 个股换手率中位数 | validated_recent | Comein Finance MCP screener returned the complete A-share cross-section; distributions use positive finite values and documented period proxies |
| `volume_ratio_20d_median` | 20日均量比中位数 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `bias_20d_median` | 20日乖离率中位数 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `atr_14d_median` | 14日ATR中位数 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `rsi_14d_median` | 14日RSI中位数 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `advance_ratio_20d` | 上涨家数占比(20日) | proxy_only | The available source maps a different period to this indicator; no chart is generated from the proxy |
| `high_60d_ratio` | 60日新高占比 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `high_120d_ratio` | 120日新高占比 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `high_250d_ratio` | 250日新高占比 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `above_ma20_ratio` | 站上20日均线占比 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `above_ma60_ratio` | 站上60日均线占比 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
| `above_ma120_ratio` | 站上120日均线占比 | blocked | stock_a_lg_indicator 在当前 AkShare 版本不存在；需接入批量供应商数据 |
