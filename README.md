# OmniSignal

## Quant 统一启动入口

双击根目录 **Quant.bat**，选择 **1 启动分析面板** 或 **2 更新数据**。
更新选项直接复用现有 `update_data.ps1 → datahub.sync run` 的提取、原始存放、规范化整理和报告流程，不改变来源与存储规则。
菜单还可查看更新报告、关闭面板；退出菜单不影响后台面板。详细用法见 [启动与更新](docs/启动与更新.md)。

OmniSignal 是一个基于 Terminal 模板构建的 A 股多维度市场监测终端。Terminal 仓库只作为只读参考；本仓库独立维护新的数据源、指标配置、DuckDB 数据和页面产物。

当前指标目录来自：`A股多维度市场监测指标库_筛选后.xlsx`，共 76 条指标，按市场交易与情绪、指数与估值、股指期货与期权、全市场横截面四个主题组织。

## 当前运行方式

```powershell
.venv-win\Scripts\python.exe scripts\fetch_seed_indicators.py --start 2020-01-01
.venv-win\Scripts\python.exe -m streamlit run macro_replay\streamlit_app.py
```

首批已接入并生成图表的序列为 5 条宽基指数收盘价。融资融券和指数 PE 暂不绘图，原因是数据口径或真实接口仍需确认。

如果需要复用已有的 iFinD MCP 配置，可通过环境变量指定只读配置路径：

```powershell
$env:OMNISIGNAL_MCP_CONFIG = "C:\\path\\to\\mcp_servers.toml"
.venv-win\Scripts\python.exe scripts\check_ifind_mcp.py
```

不要把真实令牌提交到 Git；Terminal 仓库仍作为只读参考。

This repository bootstraps a local "复盘数据库" that pulls structured macro and market datasets via the Hexin iFind MCP servers, stores them inside DuckDB, and renders indicator dashboards based on reusable templates. No Excel/CSV exports are required—everything lives inside the embedded database.

## Features

- **MCP ingestion**: generic client for `streamablehttp` MCP servers (stock/fund/EDB/news) with YAML-driven server definitions.
- **Indicator catalog**: declarative templates describing each macro indicator (theme, query template, default arguments, chart recipe).
- **DuckDB storage**: normalized tables for raw payloads, parsed observations, and generated charts.
- **CLI workflow**: `macro-replay` command to list indicators, fetch/update data, and render charts.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp config/mcp_servers.example.toml config/mcp_servers.toml  # fill in Authorization tokens
cp config/indicators.example.yaml config/indicators.yaml    # customize indicator templates
macro-replay init-db                                        # create DuckDB schema
macro-replay list-indicators                                # browse catalog
macro-replay fetch --indicator usd-liquidity-dxy --start 2020-01-01 --end 2026-03-15 --chart
```

Charts are saved under `charts/<indicator>/` and the structured series live inside `data/replay.duckdb`.

### Quick dashboard launch

The current Windows launcher starts the Streamlit app directly and does not run the old refresh workflow.

```powershell
cd "C:\Users\X1\Documents\Terminal 2026"
.\start_dashboard.bat
```

Or run it directly from PowerShell:

```powershell
.\start_dashboard.ps1
```

Then open `http://127.0.0.1:8000`.

## Streamlit workspace

The dashboard layer now runs on Streamlit while reusing the existing pipeline, DuckDB storage, and indicator config. Compared with the old template-based page, the new UI adds:

- theme-level date controls and refresh actions;
- multi-indicator analysis in one page;
- per-chart series toggles, resampling, rolling mean, rebasing, and common rate-of-change transforms;
- inline raw data inspection plus Excel export.

## Repository layout

```
macro_replay/      Python package (MCP client, DB, CLI, chart helpers)
config/            Server + indicator templates (copy *.example.* to real files)
data/              DuckDB database file (auto-created)
charts/            Generated chart assets organized by indicator
```

## Next steps

- Expand `config/indicators.yaml` with more themes (美元流动性、风险偏好、供需结构等)。
- Hook the CLI into Codex via a custom skill to enable natural language triggers.
- Schedule `macro-replay fetch --all` via cron/Dagster for automated daily refresh.

## 数据获取与规范化

统一数据模块位于 `datahub/sync/`，配置位于 `config/data_sync.yaml`。

- 获取：`python -m datahub.sync acquire --dataset all --end YYYY-MM-DD`
- 状态：`python -m datahub.sync status`
- 规范化：`python -m datahub.sync normalize --end YYYY-MM-DD`

同一数据集的候选来源按顺序尝试，成功一个即可；实际来源保留在数据库中。获取阶段只更新来源数据，规范化阶段检查日期对齐后发布标准快照。规则、存储边界和验收说明见 [数据获取与规范化模块](docs/数据获取与规范化模块.md)。
