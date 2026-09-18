# OmniSignal 工程交接说明

生成日期：2026-08-03  
工程目录：`C:\Users\heshu\Documents\工作台\OmniSignal`  
GitHub 仓库：`git@github.com:Rambo0224/OmniSignal.git` / `https://github.com/Rambo0224/OmniSignal.git`

这份文档的目标是让另一个 AI 或开发者在没有历史对话的情况下，能够完整理解 OmniSignal 的定位、运行方式、数据链路、数据库结构、图表渲染规则、维护边界和二次开发方法。

## 1. 一句话说明

OmniSignal 是一个面向 A 股市场的多维度交易指标面板。它把外部真实数据源抓取到本地 DuckDB 数据库，再从数据库按面板设定的展示时间窗口生成 PNG 缩略图和 HTML 交互图，最后用 Streamlit 页面展示。

它不是 Terminal 项目的子目录，也不应该继续依赖或修改 Terminal。Terminal 只作为历史模板参考。

## 2. 核心原则

这个工程长期维护时必须遵守下面几条规则：

1. 只用真实数据，不画假图，不用占位数据冒充真实指标。
2. 数据更新、数据库写入、派生因子计算，必须走专门的数据更新流程。
3. 面板上的刷新按钮只负责根据当前全局日期窗口从数据库重新读取数据并重画展示图，不允许抓取外部数据或修改数据库。
4. 数据抓取的时间范围和图表展示的时间范围是两套概念：
   - 数据抓取：尽可能多地从今天向前抓取并存库；A 股全市场日线目前最长到 2021-01-01。
   - 图表展示：只根据面板侧边栏的全局开始日期和结束日期过滤数据库数据。
5. 若要改用户没有明确要求的行为，必须先请示。尤其不要随手改图表风格、启动方式、数据库策略或 UI 交互。
6. 不要修改 `Terminal/` 参考项目。当前 OmniSignal 目录内已经不应依赖本地 Terminal 文件。
7. 外部数据源失败不能静默跳过。必须写入日志，并在总刷新流程中报告失败。
8. 所有折线图都是纯线条，不显示点；所有坐标轴不显示 0 轴横线。
9. 交互图长宽比例固定为 `1358:900`，时间轴刻度使用统一自适应算法。

这些规则也写在根目录 [SKILL.md](../SKILL.md) 中，是项目级约束。

## 3. 当前工程结构

根目录主要文件和目录如下：

```text
OmniSignal/
├─ OmniSignal.bat                 # Windows 快捷启动入口
├─ start_dashboard.ps1            # 实际启动脚本，支持直接启动或先更新数据
├─ pyproject.toml                 # Python 包定义、依赖、CLI 入口
├─ README.md                      # 历史 README，部分路径/端口说明可能是旧的
├─ START.md                       # 历史启动说明，部分路径/端口说明可能是旧的
├─ SKILL.md                       # 本项目开发规则
├─ config/
│  ├─ indicators.yaml             # 面板指标目录，当前 57 个展示指标
│  ├─ theme_settings.toml         # 面板全局展示日期、列数、模块开关
│  ├─ mcp_servers.toml            # iFinD MCP 配置，当前项目保留真实 token
│  ├─ mcp_servers.example.toml    # MCP 示例配置
│  ├─ source_registry.yaml        # 数据源登记表
│  ├─ source_capabilities.yaml    # 数据源能力说明
│  └─ comein_mcp.local.env        # 进门财经 MCP 本地配置
├─ data/
│  ├─ replay.duckdb               # 主数据库
│  ├─ baostock_universe.csv       # BaoStock A 股股票池缓存
│  ├─ baostock_trade_dates.csv    # BaoStock 交易日缓存
│  ├─ baostock_daily_history_dates.json  # 已完成 daily-all 日期检查点
│  ├─ baostock_request_budget.json       # BaoStock 日请求预算记录
│  └─ ashare_universe.csv         # A 股股票池相关缓存
├─ charts/
│  ├─ *.png                       # 首页卡片缩略图
│  └─ *.html                      # 点开后的交互图
├─ logs/
│  ├─ dashboard_refresh_all.json  # 总刷新报告
│  └─ *_refresh.json              # 各数据源/计算模块日志
├─ macro_replay/
│  ├─ streamlit_app.py            # 当前主面板
│  ├─ dashboard_service.py        # 面板读库、刷新、重绘服务
│  ├─ pipeline.py                 # 指标抓取、落库、重绘主流程
│  ├─ charts.py                   # Plotly 图表生成规则
│  ├─ db.py                       # DuckDB schema 和写入函数
│  ├─ config.py                   # YAML/TOML 配置加载
│  ├─ sources/ifind.py            # iFinD EDB/MCP 解析
│  ├─ mcp_client.py               # MCP HTTP 客户端
│  ├─ etf_catalog.py              # ETF 观察清单
│  └─ ...
├─ datahub/
│  ├─ adapters/                   # 数据源适配器：BaoStock、AkShare、iFinD、Comein、OpenBB、Tushare 等
│  ├─ transforms/                 # A 股量化/横截面计算函数
│  └─ registry/                   # 辅助数据集登记
├─ scripts/
│  ├─ fetch_all_indicators.py     # 总刷新入口
│  ├─ fetch_baostock_equity_bars.py
│  ├─ compute_baostock_market_breadth.py
│  ├─ compute_ashare_technical_factors.py
│  ├─ compute_index_derived.py
│  ├─ compute_margin_derived.py
│  ├─ compute_futures_annualized_basis.py
│  ├─ fetch_etf_bars.py
│  ├─ fetch_etf_share_history.py
│  ├─ rerender_charts.py
│  ├─ upgrade_chart_html.py
│  └─ ...
├─ tests/                         # pytest 单元测试
└─ docs/                          # 项目文档
```

## 4. 环境与依赖

项目要求 Python 3.11+。当前 Windows 优先使用：

```text
.venv-win\Scripts\python.exe
```

如果 `.venv-win` 不存在，`start_dashboard.ps1` 会尝试使用 `.venv`，或者自动创建 `.venv`。

核心依赖见 [pyproject.toml](../pyproject.toml)：

- `duckdb`
- `pandas`
- `plotly`
- `kaleido`
- `requests`
- `akshare`
- `baostock`
- `pyyaml`
- `typer`
- `fastapi`
- `uvicorn`
- `streamlit`
- `dash`
- `jinja2`
- `openpyxl`
- `tomli-w`

可选 provider：

- `tushare`
- `openbb-yfinance`

安装或修复依赖：

```powershell
cd "C:\Users\heshu\Documents\工作台\OmniSignal"
.\.venv-win\Scripts\python.exe -m pip install -e . --no-build-isolation
```

如果没有 `.venv-win`，可使用系统 Python：

```powershell
python -m pip install -e . --no-build-isolation
```

## 5. 启动方式

### 5.1 推荐：双击 BAT

根目录有：

```text
OmniSignal.bat
```

用户希望这个文件作为快捷方式留在项目目录中。

### 5.2 PowerShell 启动

```powershell
cd "C:\Users\heshu\Documents\工作台\OmniSignal"
.\start_dashboard.ps1
```

启动脚本默认端口是：

```text
http://127.0.0.1:8501
```

不要再按旧 README/START 里的 `8000` 或 Terminal 路径理解当前工程；当前主面板是 Streamlit，默认 `8501`。

### 5.3 启动菜单

运行 `start_dashboard.ps1` 后会出现：

```text
OmniSignal startup options
  1. Start dashboard directly
  2. Update all data first, then ask whether to start dashboard
```

含义：

- 选择 `1`：直接启动面板，不更新数据库。
- 选择 `2`：先执行完整增量数据更新；更新成功后询问是否启动面板；更新失败则中止并提示查看日志。

也可以通过参数绕过菜单：

```powershell
.\start_dashboard.ps1 -Mode Direct
.\start_dashboard.ps1 -Mode UpdateFirst
.\start_dashboard.ps1 -ListenHost 127.0.0.1 -Port 8501
```

### 5.4 启动脚本做了什么

[start_dashboard.ps1](../start_dashboard.ps1) 的主要职责：

1. 定位工程根目录。
2. 查找 Python 3.11+。
3. 优先使用 `.venv-win\Scripts\python.exe`，其次 `.venv\Scripts\python.exe`。
4. 如果没有虚拟环境，尝试创建 `.venv`。
5. 检查依赖是否可导入。
6. 依赖缺失时执行 `pip install -e . --no-build-isolation`。
7. 若选择更新数据，运行：

   ```powershell
   .\scripts\fetch_all_indicators.py --mode incremental
   ```

8. 数据更新失败则抛错：

   ```text
   Data refresh failed. Check logs\dashboard_refresh_all.json and provider-specific logs for details.
   ```

9. 启动前停止本项目同端口旧 Streamlit 进程。
10. 启动：

    ```powershell
    python -m streamlit run .\macro_replay\streamlit_app.py --server.headless true --server.address 127.0.0.1 --server.port 8501 --server.fileWatcherType none --browser.gatherUsageStats false
    ```

## 6. 面板行为

当前主页面文件：

```text
macro_replay/streamlit_app.py
```

页面功能：

- 左侧设置每行图表数：2、3、4。
- 左侧设置是否显示原始数据折叠区。
- 左侧配置进门财经 MCP：
  - 服务地址
  - `x-mcp-key`
  - 保存
  - 清空
- 左侧设置全局展示日期：
  - 全局开始日期
  - 全局结束日期
- 左侧按钮：刷新面板。

重要边界：

```text
刷新面板 ≠ 更新数据库
```

`streamlit_app.py` 中的“刷新面板”只调用：

```python
rerender_theme_from_db(theme.key, start_date, end_date)
```

它只从 DuckDB 读已有数据，并按当前日期窗口重新生成 `charts/*.png` 缩略图，不抓外部数据。

点开卡片有两种详情页：

- `?view=chart&indicator=<indicator_id>`：交互图。
- `?view=data&indicator=<indicator_id>`：原始数据表和 Excel 下载。

## 7. 数据更新总流程

总刷新入口：

```text
scripts/fetch_all_indicators.py
```

推荐命令：

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_all_indicators.py --mode incremental
```

全量重建：

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_all_indicators.py --mode full
```

注意：`--mode full` 会删除 `data/replay.duckdb` 和 `charts/*.html`、`charts/*.png` 后重建。除非明确需要，不要随便跑。

### 7.1 incremental 模式

`incremental` 的目标是：

- 先检查数据库已有覆盖范围。
- 只抓缺失数据或最新增量。
- 已经抓过的数据不重复抓。
- 完成后重算需要计算的指标。
- 最后按当前面板全局展示日期重绘所有主题图。

### 7.2 full 模式

`full` 的目标是：

- 删除主数据库。
- 删除所有图表产物。
- 从零重建。

风险：

- 运行时间更长。
- 需要重新访问多个数据源。
- 如果中途失败，旧数据库和旧图表已被删除。

因此，默认应使用 `incremental`。

### 7.3 总刷新实际步骤

总流程在 [macro_replay/dashboard_service.py](../macro_replay/dashboard_service.py) 的 `refresh_all_dashboard()` 中定义。当前顺序是：

1. `fetch_baostock_equity_daily_all`
   - 从 BaoStock 按交易日批量抓全 A 日线行情。
   - 当前 A 股行情最长从 `2021-01-01` 开始。
   - 写入 `equity_daily_bars`。
2. `fetch_configured_indicators`
   - 处理 `config/indicators.yaml` 中配置的指标。
   - 包括 iFinD EDB 直连、catalog 类指标、手工/多序列/期限结构等。
   - 写入 `observations`，并生成图。
3. `compute_margin_derived`
   - 计算融资融券派生指标，例如融券余量增速。
4. `compute_baostock_market_breadth`
   - 基于全 A 日线计算市场宽度：
     - 上涨家数占比
     - 涨停/跌停数量和占比
     - 全市场换手率
     - 腾落指数
     - 真实炸板率等
5. `compute_ashare_technical_factors`
   - 基于全 A 日线计算横截面技术指标：
     - 动量中位数
     - 反转中位数
     - ATR 中位数
     - RSI 中位数
     - 均线占比
     - 新高占比等
6. `compute_index_derived`
   - 计算指数波动率、科创50 RSI 等指数派生指标。
7. `compute_futures_annualized_basis`
   - 计算股指期货年化基差率。
8. `fetch_etf_daily_bars`
   - 抓 ETF 日线行情。
   - 写入 `equity_daily_bars`，source 为 ETF 相关来源。
9. `fetch_etf_share_history`
   - 抓 ETF 份额历史、份额变化、估算资金流。
   - 写入 `etf_share_daily`。
10. `rerender_theme:market-breadth`
11. `rerender_theme:index-valuation`
12. `rerender_theme:futures-options`
13. `rerender_theme:cross-sectional`

如果前 9 步任一数据准备步骤失败，则后续主题重绘会跳过，总报告状态为 `failed`。

### 7.4 进度条

总刷新时命令行会显示单行动态进度条，类似：

```text
=====>----------------------  20% 2/13 compute_margin_derived running
```

实现位置：

```text
macro_replay/dashboard_service.py
```

相关函数：

- `_print_refresh_progress()`
- `_animate_refresh_progress()`
- `_run_refresh_step()`

进度条不保证准确反映内部子任务百分比，但任务完成时必须到 100%。

## 8. 数据库

主库路径：

```text
data/replay.duckdb
```

当前检查到的数据库大小约 300 MB。当前主要表：

| 表名 | 当前行数 | 用途 |
|---|---:|---|
| `observations` | 87893 | 指标时间序列主表 |
| `equity_daily_bars` | 8148059 | 股票、指数、ETF 日线 OHLCV 行情 |
| `etf_share_daily` | 38884 | ETF 份额、份额变化、估算流入流出 |
| `charts` | 57 | 每个指标对应的 PNG/HTML 图表路径 |
| `raw_payloads` | 0 | 原始接口响应预留表 |
| `series` | 0 | 序列元数据预留表 |
| `sources` | 0 | 数据源元数据预留表 |
| `manual_import_logs` | 0 | 手工 Excel 导入日志 |

### 8.1 `observations`

schema 定义在 [macro_replay/db.py](../macro_replay/db.py)：

```sql
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
```

这是面板指标的核心表。大部分展示指标最终都落到这里。

字段说明：

- `series_id`：序列唯一名，通常为 `<indicator_id>:<factor_or_source>`。
- `indicator_id`：指标 ID，对应 `config/indicators.yaml`。
- `source_id`：数据源，如 `ifind_edb`、`baostock_derived`、`akshare_public`。
- `obs_time`：观测日期。
- `value`：数值。
- `unit`：单位。
- `extra`：JSON 扩展信息。
- `ingested_at`：入库时间。

常用查询：

```sql
SELECT indicator_id, count(*), min(obs_time), max(obs_time)
FROM observations
GROUP BY indicator_id
ORDER BY indicator_id;
```

查询单个指标：

```sql
SELECT obs_time, value, unit, source_id, extra
FROM observations
WHERE indicator_id = 'turnover-total'
ORDER BY obs_time;
```

### 8.2 `equity_daily_bars`

```sql
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
```

用途：

- 全 A 股票日线行情。
- ETF 日线行情。
- 指数或 provider 回补行情。
- 横截面指标计算的原始输入。

当前数据源覆盖：

| source_id | 行数 | 标的数 | 最早日期 | 最新日期 |
|---|---:|---:|---|---|
| `baostock_history_k_data` | 6617496 | 5410 | 2021-01-04 | 2026-07-31 |
| `openbb_yfinance_batch` | 753368 | 663 | 2021-01-04 | 2026-07-16 |
| `openbb_yfinance` | 673308 | 553 | 2021-01-04 | 2026-07-17 |
| `akshare_etf_hist_em` | 64605 | 29 | 2010-01-04 | 2026-07-20 |
| `akshare_stock_zh_a_hist_qfq` | 39021 | 40 | 2021-01-04 | 2026-07-17 |
| `baostock_etf_history_k_data` | 261 | 29 | 2026-07-21 | 2026-07-31 |

重要说明：

- A 股全市场行情当前优先使用 BaoStock。
- BaoStock 按 `daily-all` 方式一日一请求，返回全市场当天行情，效率比逐股票请求高。
- 当前最长历史规则是从 `2021-01-01` 向后；如果股票上市晚于此日期，有多少抓多少。
- ETF 行情也写入这张表，但 source_id 不同。

### 8.3 `etf_share_daily`

```sql
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
```

用途：

- 存储 ETF 份额。
- 计算份额变化。
- 用份额变化和 ETF 收盘价估算资金流入/流出金额。

当前覆盖：

| source_id | 行数 | ETF 数 | 最早日期 | 最新日期 |
|---|---:|---:|---|---|
| `akshare_etf_scale_sse` | 34831 | 26 | 2021-01-04 | 2026-07-31 |
| `akshare_etf_scale_szse_daily` | 4053 | 3 | 2021-01-04 | 2026-07-31 |

说明：

- 上交所 ETF 份额数据来自 AkShare 的上交所 ETF 规模/份额接口。
- 深交所 ETF 份额数据来自 AkShare 的深交所 ETF 每日规模/份额接口。
- 深交所当前有数据的是 3 只 watchlist ETF，具体以 `etf_share_daily` 查询为准。

### 8.4 `charts`

```sql
CREATE TABLE IF NOT EXISTS charts (
    indicator_id TEXT,
    html_path TEXT,
    image_path TEXT,
    rendered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_summary TEXT
);
```

用途：

- 记录每个指标最后一次生成的 HTML 交互图和 PNG 缩略图。
- Streamlit 首页通过这张表找图片。
- 详情页通过这张表找 HTML。

当前记录数：57。

### 8.5 数据库写入策略

常用写入函数：

- `upsert_observations()`
  - 只按最新时间追加，不回补历史缺口。
- `insert_missing_observations()`
  - 按时间戳插入缺失记录，可回补历史缺口。
- `replace_observations_since()`
  - 删除某指标某序列在某日期之后的数据并重写。
  - 适合派生指标重算。
- `insert_missing_equity_bars()`
  - 单标的 OHLCV 缺口插入。
- `insert_missing_equity_bars_bulk()`
  - 批量插入全市场 OHLCV，使用 DuckDB anti-join，适合 BaoStock daily-all。
- `insert_missing_etf_share_rows()`
  - 插入 ETF 份额缺失数据。
- `record_chart()`
  - 更新 `charts` 表中的图表路径。

## 9. 数据源

数据源登记在：

```text
config/source_registry.yaml
```

当前主要数据源：

| source_id | 类型 | 用途 | 可靠性定位 |
|---|---|---|---|
| `baostock_history_k_data` | BaoStock | 全 A 日线行情 | 当前 A 股行情优先 |
| `ifind_edb` / `ifind-edb-direct` | iFinD EDB | 成交额、两融等历史序列 | production candidate |
| `akshare_public` | AkShare | ETF、期权、指数、估值等公开接口 | fallback / 补充 |
| `akshare_etf_hist_em` | AkShare ETF 行情 | ETF 日线 | 已使用 |
| `akshare_etf_scale_sse` | AkShare ETF 份额 | 上交所 ETF 份额 | 已使用 |
| `akshare_etf_scale_szse_daily` | AkShare ETF 份额 | 深交所 ETF 份额 | 已使用 |
| `cffex_official` | 中金所官方 | 股指期货数据 | preferred |
| `comein_mcp` | 进门财经 MCP | 指数/期货等候选补充源 | production candidate |
| `openbb_yfinance` / `openbb_yfinance_batch` | OpenBB/Yahoo | A 股行情备选 | fallback |
| `tushare_pro` | Tushare Pro | 备选行情源 | 需 token |
| `manual_excel` | 手工 Excel | 兜底 | last resort |
| `derived` / `baostock_derived` | 内部计算 | 派生指标 | internal |

### 9.1 iFinD MCP

配置文件：

```text
config/mcp_servers.toml
```

包含：

- `hexin-ifind-ds-stock-mcp`
- `hexin-ifind-ds-fund-mcp`
- `hexin-ifind-ds-edb-mcp`
- `hexin-ifind-ds-news-mcp`

项目当前允许把密钥保留在工程里，并且用户曾要求可一起推送。但维护者仍应避免在文档里复制完整 token。

iFinD EDB 直连适配器：

```text
macro_replay/sources/ifind.py
```

重要函数：

- `fetch_edb_direct_dataframe()`
- `_extract_payloads()`
- `_coerce_json_object()`
- `_coerce_json_list()`
- `_payload_data_section()`
- `_iter_data_entries()`
- `_entry_table()`

注意：iFinD 返回经常不是稳定 dict，有时最外层或内层 `data`、`result`、`datas`、`entry.data` 会是 JSON 字符串。因此解析器必须保持容错，不能假设 `.get()` 一定可用。

相关测试：

```text
tests/test_ifind_market_adapter.py
```

### 9.2 进门财经 MCP

本地配置：

```text
config/comein_mcp.local.env
```

页面入口：

```text
Streamlit 侧边栏 -> 进门财经 MCP
```

适配器：

```text
datahub/adapters/comein_mcp.py
```

用户要求像 iFinD 一样，页面上可保存、清空、填写。当前页面支持：

- 服务地址输入
- `x-mcp-key` 密码输入
- 保存
- 清空

### 9.3 BaoStock

适配器：

```text
datahub/adapters/baostock_equity.py
```

刷新脚本：

```text
scripts/fetch_baostock_equity_bars.py
```

当前全 A 行情优先用 BaoStock。重要规则：

- BaoStock 不允许并发连接访问。
- 用户提醒每日 API 请求不能超过 5 万次，工程目前默认硬限制 `20,000`。
- `daily-all` 模式一日一请求获取全市场行情，避免逐股票请求过慢。
- 请求间隔默认 `0.3` 秒。
- 连续 3 次失败会停止，以保护账号。
- 成功日期写入：

  ```text
  data/baostock_daily_history_dates.json
  ```

- 请求预算写入：

  ```text
  data/baostock_request_budget.json
  ```

### 9.4 AkShare

适配器：

```text
datahub/adapters/akshare_public.py
datahub/adapters/akshare_equity.py
```

用途：

- ETF 历史行情。
- ETF 份额/规模。
- 50ETF 期权 PCR。
- QVIX/IVIX。
- 指数估值或历史数据备选。
- 涨跌停池等公开接口备选。

如果 AkShare 接口报错，应优先查看官方接口定义和参数，不能简单认定免费库只能返回几个交易日。

### 9.5 OpenBB / Tushare / Yahoo

这些是补充数据源，主要用于行情备选或补洞。当前不作为全 A 主来源。

相关文件：

```text
datahub/adapters/openbb_yfinance.py
datahub/adapters/yfinance_batch.py
datahub/adapters/tushare_pro.py
scripts/fetch_yfinance_batch_bars.py
```

## 10. 指标目录

指标主配置：

```text
config/indicators.yaml
```

当前 4 个主题、57 个面板指标：

| 主题 ID | 主题名 | 指标数 |
|---|---|---:|
| `market-breadth` | 市场交易与情绪 | 14 |
| `index-valuation` | 指数与估值 | 18 |
| `futures-options` | 股指期货与期权 | 10 |
| `cross-sectional` | 全市场横截面 | 15 |

### 10.1 市场交易与情绪

| 指标 ID | 名称 | 字段 | 来源类型 | 当前覆盖 |
|---|---|---|---|---|
| `turnover-total` | 全市场成交额 | `turnover_total` | iFinD EDB | 2000-01-04 至 2026-07-31 |
| `turnover-rate` | 全市场换手率 | `turnover_rate` | 派生/catalog | 2021-01-05 至 2026-07-31 |
| `margin-balance` | 融资余额 | `margin_balance` | iFinD EDB | 2010-03-31 至 2026-07-31 |
| `margin-purchase` | 融资买入额 | `margin_purchase` | iFinD EDB | 2010-03-31 至 2026-07-31 |
| `short-balance` | 融券余额 | `short_balance` | iFinD EDB | 2010-03-31 至 2026-07-31 |
| `short-increase-rate` | 融券余量增速 | `short_increase_rate` | 派生 | 2010-04-01 至 2026-07-31 |
| `advance-ratio` | 上涨家数占比 | `advance_ratio` | 派生/catalog | 2021-01-05 至 2026-07-31 |
| `limit-up-count` | 涨停家数 | `limit_up_count` | 派生/catalog | 2021-01-05 至 2026-07-31 |
| `limit-down-count` | 跌停家数 | `limit_down_count` | 派生/catalog | 2021-01-05 至 2026-07-31 |
| `limit-up-ratio` | 涨停家数占比 | `limit_up_ratio` | 派生/catalog | 2021-01-05 至 2026-07-31 |
| `limit-down-ratio` | 跌停家数占比 | `limit_down_ratio` | 派生/catalog | 2021-01-05 至 2026-07-31 |
| `explosive-ratio` | 真实炸板率 | `explosive_ratio` | 派生/catalog | 2021-01-05 至 2026-07-31 |
| `advance-ratio-2` | 上涨家数占比（日频） | `advance_ratio` | 派生/catalog | 2021-01-05 至 2026-07-31 |
| `advance-decline-line` | 腾落指数 | `advance_decline_line` | 派生 | 2021-01-05 至 2026-07-31 |

腾落指数计算逻辑：

```text
每日上涨家数 - 每日下跌家数 = 当日净上涨家数
腾落指数 = 从起始日开始对当日净上涨家数做累计和
```

输入来自 `equity_daily_bars` 中的全 A 日线，计算脚本是：

```text
scripts/compute_baostock_market_breadth.py
```

### 10.2 指数与估值

| 指标 ID | 名称 | 字段 | 当前覆盖 |
|---|---|---|---|
| `hs300-close` | 沪深300收盘价 | `hs300_close` | 2020-01-02 至 2026-07-17 |
| `hs300-pe` | 沪深300市盈率 | `hs300_pe` | 2025-04-18 至 2026-07-17 |
| `hs300-pb` | 沪深300市净率 | `hs300_pb` | 2025-04-18 至 2026-07-17 |
| `sz50-close` | 上证50收盘价 | `sz50_close` | 2020-01-02 至 2026-07-17 |
| `sz50-pe` | 上证50市盈率 | `sz50_pe` | 2025-04-18 至 2026-07-17 |
| `sz50-pb` | 上证50市净率 | `sz50_pb` | 2025-04-18 至 2026-07-17 |
| `cyb50-close` | 创业板50收盘价 | `cyb50_close` | 2020-01-02 至 2026-07-17 |
| `cyb50-pe` | 创业板50市盈率 | `cyb50_pe` | 2025-05-06 至 2026-07-17 |
| `cyb50-pb` | 创业板50市净率 | `cyb50_pb` | 2025-05-06 至 2026-07-17 |
| `kc50-close` | 科创50收盘价 | `kc50_close` | 2020-01-02 至 2026-07-17 |
| `kc50-pe` | 科创50市盈率 | `kc50_pe` | 2025-04-24 至 2026-07-17 |
| `kc50-pb` | 科创50市净率 | `kc50_pb` | 2025-04-24 至 2026-07-17 |
| `zz500-close` | 中证500收盘价 | `zz500_close` | 2020-01-02 至 2026-07-17 |
| `hs300-vol-20d` | 沪深300 20日波动率 | `hs300_vol_20d` | 2020-02-07 至 2026-07-17 |
| `sz50-vol-20d` | 上证50 20日波动率 | `sz50_vol_20d` | 2020-02-07 至 2026-07-17 |
| `cyb50-vol-20d` | 创业板50 20日波动率 | `cyb50_vol_20d` | 2020-02-07 至 2026-07-17 |
| `kc50-vol-20d` | 科创50 20日波动率 | `kc50_vol_20d` | 2020-02-07 至 2026-07-17 |
| `kc50-rsi-14d` | 科创50 14日RSI | `kc50_rsi_14d` | 2020-01-22 至 2026-07-17 |

指数 20 日波动率：

```text
日收益率 = close_t / close_{t-1} - 1
20日波动率 = rolling_std(日收益率, 20) * sqrt(252)
```

科创50 RSI：

```text
delta = close.diff()
gains = max(delta, 0) 的 14 日均值
losses = -min(delta, 0) 的 14 日均值
RS = gains / losses
RSI = 100 - 100 / (1 + RS)
```

### 10.3 股指期货与期权

| 指标 ID | 名称 | 字段 | 当前覆盖 |
|---|---|---|---|
| `if-vol-20d` | IF主力连续波动率 | `if_vol_20d` | 2025-02-07 至 2026-07-17 |
| `ic-vol-20d` | IC主力连续波动率 | `ic_vol_20d` | 2025-02-07 至 2026-07-17 |
| `if-basis` | IF基差 | `if_basis` | 2025-01-02 至 2026-07-17 |
| `ic-basis` | IC基差 | `ic_basis` | 2025-01-02 至 2026-07-17 |
| `if-basis-annual` | IF年化基差率 | `if_basis_annual` | 2025-01-02 至 2026-07-16 |
| `ic-basis-annual` | IC年化基差率 | `ic_basis_annual` | 2025-01-02 至 2026-07-16 |
| `pcr-volume` | 认沽认购比（成交量） | `pcr_volume` | 2015-02-09 至 2026-07-17 |
| `pcr-oi` | 认沽认购比（持仓量） | `pcr_oi` | 2015-02-09 至 2026-07-17 |
| `ivix-50` | 上证50ETF隐含波动率 | `ivix_50` | 2015-02-09 至 2026-07-17 |
| `qvix` | QVIX恐慌指数 | `qvix` | 2015-02-09 至 2026-07-17 |

基差：

```text
基差 = 股指期货主力合约价格 - 对应现货指数点位
```

年化基差率：

```text
原始基差率 = futures / spot - 1
年化基差率 = 原始基差率 * 365 / 剩余到期天数
```

相关函数：

```text
datahub/transforms/ashare.py
scripts/compute_futures_annualized_basis.py
```

### 10.4 全市场横截面

这些指标都从 `equity_daily_bars` 的全 A 日线计算，优先使用 BaoStock 数据。

计算脚本：

```text
scripts/compute_ashare_technical_factors.py
```

核心公式在：

```text
datahub/transforms/ashare.py
```

| 指标 ID | 名称 | 字段 | 当前覆盖 |
|---|---|---|---|
| `momentum-20d-median` | 20日动量中位数 | `momentum_20d_median` | 2021-02-01 至 2026-07-31 |
| `momentum-60d-median` | 60日动量中位数 | `momentum_60d_median` | 2021-04-06 至 2026-07-31 |
| `reversal-5d-median` | 5日反转中位数 | `reversal_5d_median` | 2021-01-11 至 2026-07-31 |
| `turnover-individual-median` | 个股换手率中位数 | `turnover_individual_median` | 2021-01-04 至 2026-07-31 |
| `volume-ratio-20d-median` | 20日均量比中位数 | `volume_ratio_20d_median` | 2021-01-29 至 2026-07-31 |
| `bias-20d-median` | 20日乖离率中位数 | `bias_20d_median` | 2021-01-29 至 2026-07-31 |
| `atr-14d-median` | 14日ATR中位数 | `atr_14d_median` | 2021-01-21 至 2026-07-31 |
| `rsi-14d-median` | 14日RSI中位数 | `rsi_14d_median` | 2021-01-22 至 2026-07-31 |
| `advance-ratio-20d` | 上涨家数占比（20日） | `advance_ratio_20d` | 2021-01-29 至 2026-07-31 |
| `high-60d-ratio` | 60日新高占比 | `high_60d_ratio` | 2021-01-29 至 2026-07-31 |
| `high-120d-ratio` | 120日新高占比 | `high_120d_ratio` | 2021-01-29 至 2026-07-31 |
| `high-250d-ratio` | 250日新高占比 | `high_250d_ratio` | 2021-01-29 至 2026-07-31 |
| `above-ma20-ratio` | 站上20日均线占比 | `above_ma20_ratio` | 2021-01-29 至 2026-07-31 |
| `above-ma60-ratio` | 站上60日均线占比 | `above_ma60_ratio` | 2021-01-29 至 2026-07-31 |
| `above-ma120-ratio` | 站上120日均线占比 | `above_ma120_ratio` | 2021-01-29 至 2026-07-31 |

公式概要：

```text
20日动量 = close_t / close_{t-20} - 1
60日动量 = close_t / close_{t-60} - 1
5日反转 = close_t / close_{t-5} - 1
个股换手率 = BaoStock turnover_rate / 100
20日均量比 = volume / MA(volume, 20)
20日乖离率 = (close - MA(close, 20)) / MA(close, 20)
14日 ATR = MA(True Range, 14)
14日 RSI = 100 - 100 / (1 + RS)
20日上涨家数占比 = mean(close > open) 再 rolling(20).mean()
N日新高占比 = mean(close >= 前 N 日最高 close) 再 rolling(20).mean()
站上 N 日均线占比 = mean(close > MA(close, N)) 再 rolling(20).mean()
```

对带 `median` 的指标：

```text
先对每只股票逐日计算指标，再按日期取全市场中位数。
```

### 10.5 ETF 数据

ETF 不一定展示在主面板，但数据已入库。观察清单在：

```text
macro_replay/etf_catalog.py
```

当前 watchlist 共 29 只，包括宽基和行业 ETF：

```text
510050 上证50ETF
510300 沪深300ETF
159919 沪深300ETF(深市)
510500 中证500ETF
512100 中证1000ETF
588000 科创50ETF
159915 创业板ETF
510880 红利ETF
512880 证券ETF
512010 医药ETF
512800 银行ETF
512070 非银ETF
512480 半导体ETF
159995 芯片ETF
512690 酒ETF
515170 食品饮料ETF
512720 计算机ETF
512980 传媒ETF
515030 新能源车ETF
515790 光伏ETF
512660 军工ETF
512400 有色金属ETF
515220 煤炭ETF
515210 钢铁ETF
516020 化工ETF
512200 房地产ETF
512170 医疗ETF
516810 农业ETF
515880 通信ETF
```

ETF 行情：

```text
scripts/fetch_etf_bars.py
```

ETF 份额变化：

```text
scripts/fetch_etf_share_history.py
```

ETF 资金流估算：

```text
share_change = shares_t - shares_{t-1}
estimated_flow_amount = share_change * close
```

## 11. 图表规则

图表生成文件：

```text
macro_replay/charts.py
```

当前全局规则：

- 首页缩略图：PNG。
- 详情页：HTML 交互图。
- 线图全部纯线条：

  ```python
  LINE_TRACE_MODE = "lines"
  ```

- 不显示点。
- 不显示 0 轴横线：

  ```python
  zeroline=False
  ```

- 交互图尺寸：

  ```python
  INTERACTIVE_CHART_WIDTH = 1358
  INTERACTIVE_CHART_HEIGHT = 900
  ```

- 交互图比例：

  ```text
  1358 / 900
  ```

- HTML 页面中 `#chart-root` 使用：

  ```css
  width: min(100vw, calc(100vh * 1358 / 900));
  aspect-ratio: 1358 / 900;
  ```

### 11.1 时间轴自适应算法

所有图表统一使用时间轴算法，不允许只针对某一张图硬编码。

Python 侧：

```text
_estimate_time_tick_count()
_estimate_time_tickformat()
```

HTML 侧：

```text
applyResponsiveTimeAxis()
```

规则：

- 根据图表宽度估算最大刻度数。
- 根据数据跨度决定日期显示格式：
  - 小于等于 75 天：`YYYY-MM-DD`
  - 小于等于 900 天：`YYYY-MM`
  - 更长：`YYYY`
- 删除旧的固定 `dtick`、`tickangle`、`ticklabelmode`。
- 使用 `tickmode="auto"`。

### 11.2 PNG 生成

PNG 由 Plotly/Kaleido 生成。为避免 Kaleido 浏览器清理阶段偶发报错导致已成功生成的 PNG 被误判失败，`_write_compact_image()` 做了容错：

- 如果子进程报错但 PNG 文件已生成且非空，只发 warning。
- 如果 PNG 不存在或为空，则必须报错。

### 11.3 图表质量控制

质量控制文件：

```text
macro_replay/data_quality.py
```

规则：

- 不允许画代理数据或占位数据。
- 如果当前展示窗口没有可用真实数据，页面显示提示，不画错误图。
- 如果图表被质量规则拦截，`load_dashboard_cards()` 会返回 `chart_available=False` 和原因。

## 12. 配置文件说明

### 12.1 `config/theme_settings.toml`

当前结构：

```toml
[app]
columns_count = "4"
start_date = "2026-01-01"
end_date = "2026-08-03"

[themes.market-breadth]
enabled = "true"

[themes.index-valuation]
enabled = "true"

[themes.futures-options]
enabled = "true"

[themes.cross-sectional]
enabled = "true"
```

重要：

- `app.start_date` / `app.end_date` 是面板展示窗口。
- 不应拿它们去限制数据库抓取。
- 各主题目前只保留 `enabled`，不再各自维护日期窗口。

### 12.2 `config/indicators.yaml`

每个指标结构大致如下：

```yaml
- id: turnover-total
  title: 全市场成交额
  source: ifind-edb-direct
  arguments:
    start_date: '2025-07-17'
  series:
    - code: turnover_total
      source: ifind-edb-direct
      query: '{code} {start_date}至{end_date}'
      query_code: A股成交额
      value_column: 沪深两市:股票:成交金额
      unit_override: 元
  chart:
    type: line
    x_field: date
    y_field: turnover_total
    title: 全市场成交额
    unit: 亿元
    y_label: 亿元
    source_label: ifind_edb
  metadata:
    factor_name: turnover_total
    indicator_type: 原始
    preferred_source: ifind_edb
    source_status: validated_historical
```

关键字段：

- `id`：唯一指标 ID。必须和图表文件名、数据库 `indicator_id` 对齐。
- `theme`：由所在 YAML 主题决定。
- `title`：页面显示名。
- `source`：处理方式，如 `catalog`、`ifind-edb-direct`。
- `arguments`：
  - 可包含 `start_date` / `end_date`。
  - 可包含 `fetch_start_date`，用于抓取窗口。
  - 注意：展示窗口不应该回写到这里。
- `series`：原始序列配置。
- `chart`：
  - `type`
  - `x_field`
  - `y_field`
  - `unit`
  - `source_label`
- `metadata`：
  - `factor_name`
  - `indicator_type`
  - `preferred_source`
  - `fallback_source`
  - `source_status`
  - `dependencies`

## 13. 代码主流程

### 13.1 配置加载

文件：

```text
macro_replay/config.py
```

核心对象：

- `MCPServer`
- `Indicator`
- `Theme`

核心函数：

- `load_mcp_servers()`
- `load_indicators()`
- `load_themes()`
- `load_app_settings()`
- `save_app_settings()`
- `load_theme_settings()`
- `save_theme_settings()`
- `find_indicator()`

ETF 的主题和指标通过 `macro_replay/etf_catalog.py` 动态构造，但当前没有要求展示在主面板。

### 13.2 指标处理

文件：

```text
macro_replay/pipeline.py
```

核心函数：

- `process_all()`
  - 处理全部配置指标。
- `process_theme(theme_key, start_date, end_date)`
  - 处理某主题。
- `process_indicator(indicator, servers)`
  - 处理单个指标。
- `fetch_series_frame()`
  - 根据 source 类型抓取单个序列。
- `rerender_indicator_from_db()`
  - 从 DB 重画单个指标。
- `rerender_theme_from_db(theme_key, start_date, end_date)`
  - 从 DB 重画某主题。
- `_apply_chart_transforms()`
  - 应用图表配置中的 transforms。
- `_apply_calculation()`
  - 应用简单计算表达式。
- `_apply_plot_date_window()`
  - 根据展示窗口裁剪图表数据。

`pipeline.py` 中默认抓取起点：

```python
DEFAULT_FETCH_START_DATE = "2000-01-01"
```

但 A 股全市场行情的实际最长起点由 BaoStock 更新脚本控制，目前是 `2021-01-01`。

### 13.3 面板服务层

文件：

```text
macro_replay/dashboard_service.py
```

职责：

- 管理全局展示窗口。
- 读数据库构造图表数据。
- 读 `charts` 表生成卡片信息。
- 执行总刷新流程。
- 写 `logs/dashboard_refresh_all.json`。
- 输出动态进度条。

常用函数：

- `load_display_window()`
- `save_display_window()`
- `build_plot_dataframe()`
- `build_indicator_dataset()`
- `load_dashboard_cards()`
- `refresh_all_dashboard()`

### 13.4 Streamlit 展示层

文件：

```text
macro_replay/streamlit_app.py
```

职责：

- 页面布局。
- 侧边栏设置。
- 首页卡片。
- 交互图详情页。
- 原始数据页。
- 从数据库按展示窗口重绘 PNG。

不要在这里新增外部数据抓取逻辑。

## 14. 添加新指标的方法

### 14.1 判断指标类型

先判断新指标属于哪类：

1. 外部已有历史序列
   - 优先写到 `config/indicators.yaml`，用现有 source 类型。
2. 需要从全 A 日线计算
   - 优先扩展 `datahub/transforms/ashare.py` 和对应 compute 脚本。
3. 需要新数据源
   - 先新增 `datahub/adapters/<source>.py`。
   - 再新增 `scripts/fetch_<source>.py`。
   - 最后接到 `refresh_all_dashboard()`。
4. 只是展示已有数据库字段
   - 只改配置和重绘图表即可。

### 14.2 新增 YAML 指标

在 `config/indicators.yaml` 对应主题下加：

```yaml
- id: new-indicator-id
  title: 新指标名称
  description: ''
  source: catalog
  server: ''
  tool: ''
  arguments:
    start_date: '2018-01-01'
  series: []
  chart:
    type: line
    x_field: date
    y_field: new_factor_name
    title: 新指标名称
    unit: ''
    y_label: 数值
    source_label: derived
  metadata:
    factor_name: new_factor_name
    indicator_type: 合成
    preferred_source: derived
    fallback_source: null
    source_status: formula_pending
    dependencies: []
```

### 14.3 新增计算因子

如果是全 A 横截面因子：

1. 在 `datahub/transforms/ashare.py` 的 `cross_section_statistics()` 中添加计算列。
2. 在 `scripts/compute_ashare_technical_factors.py` 的 `FACTORS` 集合中加入 factor 名。
3. 确保 `config/indicators.yaml` 有同名 `metadata.factor_name`。
4. 运行：

   ```powershell
   .\.venv-win\Scripts\python.exe .\scripts\compute_ashare_technical_factors.py
   ```

5. 再重绘：

   ```powershell
   .\.venv-win\Scripts\python.exe .\scripts\rerender_charts.py
   ```

或直接跑总刷新：

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_all_indicators.py --mode incremental
```

### 14.4 新增数据源

推荐结构：

```text
datahub/adapters/new_source.py
scripts/fetch_new_source.py
tests/test_new_source.py
```

数据源脚本需要：

- 返回结构化 `report`：
  - `started_at`
  - `source_id`
  - `success`
  - `failed`
  - `skipped`
  - `finished_at`
- 写入 `logs/<source>_refresh.json`。
- 失败时不能默默吞掉。
- 入库时使用 `insert_missing_*` 或 `replace_observations_since()`。

如果要纳入启动前总刷新，需要在：

```text
macro_replay/dashboard_service.py
```

的 `refresh_all_dashboard()` 里新增 `_run_refresh_step()`。

新增前必须确认这属于用户授权范围。

## 15. 常用操作命令

### 15.1 启动面板

```powershell
cd "C:\Users\heshu\Documents\工作台\OmniSignal"
.\start_dashboard.ps1
```

### 15.2 直接启动，不弹菜单

```powershell
.\start_dashboard.ps1 -Mode Direct
```

### 15.3 先更新数据再启动

```powershell
.\start_dashboard.ps1 -Mode UpdateFirst
```

### 15.4 单独执行总增量刷新

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_all_indicators.py --mode incremental
```

### 15.5 全量重建

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_all_indicators.py --mode full
```

### 15.6 只抓 BaoStock 全 A 日线

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_baostock_equity_bars.py --mode daily-all --start 2021-01-01 --end 2026-08-03
```

实际启动总刷新时，会自动使用“最新已完成交易日”作为数据截止日，一般不会用当天未完成日线。

### 15.7 只计算市场宽度

```powershell
.\.venv-win\Scripts\python.exe .\scripts\compute_baostock_market_breadth.py
```

### 15.8 只计算横截面技术因子

```powershell
.\.venv-win\Scripts\python.exe .\scripts\compute_ashare_technical_factors.py
```

### 15.9 只计算指数派生指标

```powershell
.\.venv-win\Scripts\python.exe .\scripts\compute_index_derived.py
```

### 15.10 只更新 ETF 行情

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_etf_bars.py
```

### 15.11 只更新 ETF 份额

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_etf_share_history.py
```

### 15.12 重绘全部图

```powershell
.\.venv-win\Scripts\python.exe .\scripts\rerender_charts.py
```

### 15.13 升级已有 HTML 图表模板

```powershell
.\.venv-win\Scripts\python.exe .\scripts\upgrade_chart_html.py
```

这个脚本用于把已有 HTML 文件升级到当前图表容器比例和时间轴自适应规则。

### 15.14 运行测试

```powershell
.\.venv-win\Scripts\python.exe -m pytest
```

常用局部测试：

```powershell
.\.venv-win\Scripts\python.exe -m pytest tests\test_ifind_market_adapter.py tests\test_charts.py tests\test_dashboard_refresh.py tests\test_dashboard_service.py
```

## 16. 日志体系

所有更新流程都必须写日志。常见日志：

| 日志 | 用途 |
|---|---|
| `logs/dashboard_refresh_all.json` | 总刷新报告，最重要 |
| `logs/baostock_equity_refresh.json` | BaoStock 全 A 行情 |
| `logs/baostock_market_breadth_refresh.json` | 市场宽度/腾落线等 |
| `logs/ashare_technical_factors_refresh.json` | 全 A 横截面技术因子 |
| `logs/index_derived_refresh.json` | 指数波动率、科创50 RSI 等 |
| `logs/margin_derived_refresh.json` | 两融派生 |
| `logs/futures_annualized_basis_refresh.json` | 股指期货年化基差 |
| `logs/etf_bars_refresh.json` | ETF 行情 |
| `logs/etf_share_history_refresh.json` | ETF 份额 |
| `logs/streamlit*.log` | 面板运行输出 |

判断总刷新是否成功：

```powershell
.\.venv-win\Scripts\python.exe -c "import json;print(json.load(open('logs/dashboard_refresh_all.json',encoding='utf-8'))['status'])"
```

期望输出：

```text
success
```

查看失败步骤：

```powershell
.\.venv-win\Scripts\python.exe - <<'PY'
import json
report = json.load(open('logs/dashboard_refresh_all.json', encoding='utf-8'))
for step in report.get('steps', []):
    if step.get('status') != 'success':
        print(step.get('name'), step.get('status'), step.get('error'))
PY
```

PowerShell 不支持 Bash heredoc，上面写法在 PowerShell 要改成：

```powershell
@'
import json
report = json.load(open('logs/dashboard_refresh_all.json', encoding='utf-8'))
for step in report.get('steps', []):
    if step.get('status') != 'success':
        print(step.get('name'), step.get('status'), step.get('error'))
'@ | .\.venv-win\Scripts\python.exe -
```

## 17. 常见问题与排错

### 17.1 页面打不开

检查：

1. 是否已有旧 Streamlit 进程占用 8501。
2. 是否在项目根目录运行。
3. 依赖是否安装。
4. 启动脚本是否报错。

命令：

```powershell
.\start_dashboard.ps1 -Mode Direct
```

如果失败，查看：

```text
logs/streamlit.stderr.log
logs/streamlit.err.log
```

### 17.2 更新数据失败

先看：

```text
logs/dashboard_refresh_all.json
```

如果错误类似：

```text
'str' object has no attribute 'get'
```

多半是外部源返回结构变了，尤其是 iFinD MCP/EDB 返回 JSON 字符串嵌套。优先修：

```text
macro_replay/sources/ifind.py
```

并加测试：

```text
tests/test_ifind_market_adapter.py
```

### 17.3 图表为空

先区分三种情况：

1. 数据库没有这个指标的数据。
2. 数据库有数据，但不在当前面板全局展示日期窗口内。
3. 数据被 `data_quality.py` 拦截。

查询数据库：

```powershell
@'
import duckdb
con = duckdb.connect('data/replay.duckdb', read_only=True)
print(con.execute("""
select count(*), min(obs_time), max(obs_time)
from observations
where indicator_id = 'turnover-rate'
""").fetchall())
con.close()
'@ | .\.venv-win\Scripts\python.exe -
```

如果数据库有数据但图为空，检查：

```text
config/theme_settings.toml
```

里的全局 `start_date` / `end_date` 是否覆盖该指标日期范围。

### 17.4 首页有 PNG，但详情页交互图没图

检查：

1. `charts` 表里的 `html_path` 是否存在。
2. `charts/<indicator>.html` 文件是否存在。
3. HTML 内是否包含：

   ```text
   function applyResponsiveTimeAxis()
   ```

4. Streamlit 详情页是否正确读取 HTML 文件。

### 17.5 Streamlit 报 DuplicateElementId

历史上因为多个 `st.plotly_chart()` 参数相同导致过。当前规则是首页使用 PNG，不嵌入 Plotly 图；详情页使用 `components.html()` 嵌入 HTML 文件。因此如果再次出现，说明有人又把首页改回了直接 `st.plotly_chart()`，应改回 PNG 缩略图策略。

### 17.6 Plotly 报 y 字段不存在

典型错误：

```text
Value of 'y' is not the name of a column in 'data_frame'
```

排查：

1. 查 `config/indicators.yaml` 的 `chart.y_field`。
2. 查 `observations.series_id` 转 wide 后实际列名。
3. 查 `build_plot_dataframe()` 是否在单列情况下自动 rename。
4. 查 compute 脚本写入的 factor 名是否和 `y_field` 一致。

### 17.7 BaoStock 卡住或被限制

规则：

- 不并发。
- 保持请求间隔。
- 尊重 `data/baostock_request_budget.json`。
- 连续 3 次失败停止。
- 已成功日期写 checkpoint，不重复抓。

如果 BaoStock 返回错误码，需要查 BaoStock 文档或官方返回解释。不要简单跳过。

### 17.8 乱码

很多老输出在 Windows 控制台显示会乱码，但文件本身通常是 UTF-8。排查时设置：

```powershell
$env:PYTHONIOENCODING='utf-8'
```

Python 读取文件时使用：

```python
Path(path).read_text(encoding='utf-8')
```

## 18. Git 与文件提交

用户曾明确要求：

- 代码要推到 `Rambo0224/OmniSignal.git`。
- 相关文件、数据库、日志可以推。
- 虚拟环境不要推。
- 密钥类配置用户允许保留和推送。

但实际维护时仍要注意：

- 不提交 `.venv-win/`、`.venv/`。
- 不提交 DuckDB WAL 文件，如 `data/replay.duckdb.wal`。
- 当前 `data/replay.duckdb` 可能被本地 Git 标记为 `skip-worktree`，不要贸然改这个标记。
- 提交前必须看：

  ```powershell
  git status --short
  ```

- 如果工作区已有与当前任务无关的变动，不要顺手提交。

常用 Git：

```powershell
git status --short
git add <明确需要提交的文件>
git commit -m "说明"
git push origin master
```

当前远端：

```text
origin git@github.com:Rambo0224/OmniSignal.git
```

## 19. 当前已知状态

截至 2026-08-03 本地数据库检查结果：

- `data/replay.duckdb` 存在，约 300 MB。
- `equity_daily_bars`：约 814.8 万行。
- BaoStock 全 A 日线覆盖到 2026-07-31。
- `observations`：87893 行。
- `etf_share_daily`：38884 行。
- `charts`：57 条记录。
- 最近图表渲染时间在 2026-08-03 09:00 至 09:06 附近。

当前 `theme_settings.toml`：

```text
全局展示开始日期：2026-01-01
全局展示结束日期：2026-08-03
每行图表数：4
```

注意：这只是展示窗口，不是数据抓取窗口。

## 20. 另一个 AI 接手时的推荐检查清单

接手后先做这些只读检查：

```powershell
cd "C:\Users\heshu\Documents\工作台\OmniSignal"
git status --short
.\.venv-win\Scripts\python.exe -m pytest tests\test_ifind_market_adapter.py tests\test_charts.py tests\test_dashboard_refresh.py tests\test_dashboard_service.py
```

检查数据库：

```powershell
@'
import duckdb
con = duckdb.connect('data/replay.duckdb', read_only=True)
print(con.execute("select count(*) from observations").fetchone())
print(con.execute("select count(*) from equity_daily_bars").fetchone())
print(con.execute("select count(*) from charts").fetchone())
con.close()
'@ | .\.venv-win\Scripts\python.exe -
```

检查总刷新日志：

```powershell
@'
import json
from pathlib import Path
p = Path('logs/dashboard_refresh_all.json')
if p.exists():
    r = json.loads(p.read_text(encoding='utf-8'))
    print(r.get('status'), r.get('completed_steps'), '/', r.get('total_steps'), r.get('progress_percent'))
    for step in r.get('steps', []):
        if step.get('status') != 'success':
            print('BAD', step.get('name'), step.get('status'), step.get('error'))
else:
    print('no dashboard_refresh_all.json')
'@ | .\.venv-win\Scripts\python.exe -
```

检查 HTML 是否升级：

```powershell
@'
from pathlib import Path
htmls = list(Path('charts').glob('*.html'))
missing = [p.name for p in htmls if 'function applyResponsiveTimeAxis()' not in p.read_text(encoding='utf-8', errors='ignore')]
print(len(htmls), 'html files')
print('missing responsive time axis:', missing)
'@ | .\.venv-win\Scripts\python.exe -
```

如果需要实际更新：

```powershell
.\.venv-win\Scripts\python.exe .\scripts\fetch_all_indicators.py --mode incremental
```

如果需要打开页面：

```powershell
.\start_dashboard.ps1 -Mode Direct
```

## 21. 最重要的维护提醒

这个项目的价值在于“真实数据 + 可解释计算 + 稳定落库 + 清晰展示”。不要为了让页面看起来完整而伪造数据，也不要为了修一个视觉问题随手改数据更新逻辑。

最安全的工作方式是：

```text
先看配置 -> 再看数据库 -> 再看对应脚本 -> 最后改最小范围代码 -> 跑测试 -> 跑目标刷新 -> 看日志 -> 再交付
```

如果问题涉及外部数据源，必须把失败原因写入日志；如果问题涉及图表展示，必须确认展示窗口只是读库过滤；如果问题涉及新指标，必须明确它是原始数据、派生数据，还是仅展示已有字段。

