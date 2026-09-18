# 复盘面板工程完整说明书

本文档的目标不是介绍“怎么用一下这个程序”，而是让任何一个有编码能力的 AI 或工程师，在没有当前对话上下文的情况下，仅凭这份文档就能在 macOS 或其他环境中复刻出当前这套工程的核心功能、目录结构、配置方式、数据流、页面行为和已知坑位。

---

## 1. 项目定位

这是一个“宏观与主题数据复盘面板”工程，不是单一的 dashboard。

当前产品层级是：

- 总面板名称：`复盘面板`
- 模块 1：`流动性面板`
- 模块 2：`有色金属数据`

这套工程的设计目标是：

- 用一套统一底座支持多个主题模块
- 模块之间复用数据抓取、数据库、图表、网页展示、原始数据下载能力
- 新增模块时尽量以“新增配置 + 少量特定逻辑”为主，而不是重写整套系统

当前已经明确的产品原则：

- 总启动器只负责启动总面板，不自动全量抓所有模块数据
- 每个模块在网页上有自己的时间范围和“刷新数据”按钮
- 模块时间设置要持久化，下一次打开继续沿用
- 首页卡片使用 `png` 缩略图
- 点击“查看交互图”打开 `html` 交互图
- 点击“查看原始数据”打开明细页，并支持下载 Excel

---

## 2. 当前目录结构

项目根目录在文档中统一记为：`<PROJECT_ROOT>`

说明：

- `project root` 指整个工程所在目录
- 不要在文档、配置说明、迁移说明里写死某一台机器的绝对路径
- 任何路径都应尽量写成相对 `project root` 的形式
- 例如：
  - `macro_replay/config.py`
  - `web/server.py`
  - `config/theme_settings.toml`
  - `data/replay.duckdb`

关键结构如下：

- `macro_replay/`
  - 核心 Python 包
  - 包含配置读取、数据库、图表生成、主处理流程、CLI
- `macro_replay/sources/`
  - 数据源适配层
  - 当前主要是 `fred.py`、`ifind.py`
- `web/`
  - FastAPI 服务
  - Jinja2 模板
- `config/`
  - 配置目录
- `config/modules/`
  - 模块扩展配置目录
- `data/replay.duckdb`
  - 主数据库
- `charts/`
  - 所有生成出来的 `html` 和 `png`
- `scripts/`
  - 维护脚本、重渲染脚本、升级脚本
- `docs/`
  - 说明文档
- `run_dashboard.ps1`
  - Windows PowerShell 启动器
- `run_dashboard.bat`
  - Windows 双击入口

---

## 3. 技术栈与依赖

### 3.1 Python 与包

最低建议环境：

- Python `3.11`
- `pip`

项目关键依赖：

- `duckdb`
- `pandas`
- `plotly`
- `kaleido`
- `requests`
- `pyyaml`
- `typer`
- `fastapi`
- `uvicorn`
- `jinja2`
- `python-multipart`
- `openpyxl`
- `tomli-w`

当前工程运行依赖的几个重点：

- `plotly + kaleido`：生成 `png` 缩略图和 `html` 交互图
- `duckdb`：存所有观测值与图表记录
- `fastapi + jinja2`：网页服务
- `openpyxl`：原始数据导出 Excel

### 3.2 当前 Windows 虚拟环境

当前实际可用虚拟环境是：

- `.venv-win`

注意：

- 不要误用系统 Python 启动服务
- 当前项目应优先使用 `.venv-win\Scripts\python.exe`

---

## 4. 启动逻辑

### 4.1 当前产品逻辑

总启动器只做一件事：

- 启动网页服务

它不再做的事情：

- 不再开机自动全量抓所有指标
- 不再默认刷新所有模块

### 4.2 模块刷新逻辑

每个模块在网页上有：

- 开始时间
- 结束时间
- `刷新数据`

点击模块自己的 `刷新数据` 后：

- 只刷新该模块
- 把这次时间设置保存下来
- 下次打开网页继续沿用

### 4.3 当前持久化设置文件

模块时间设置保存在：

- `config/theme_settings.toml`

示例结构：

```toml
[themes.usd-liquidity]
start_date = "2015-01-01"
end_date = "2026-03-23"
enabled = "true"

[themes.commodity-review]
start_date = "2022-01-01"
end_date = "2024-12-31"
enabled = "true"
```

规则：

- 只要某模块在这里存在记录，就视为已启用
- 网页展示时优先读这里

---

## 5. Web 功能要求

### 5.1 首页

首页功能：

- 展示总标题 `复盘面板`
- 按模块分区显示卡片
- 每个模块有独立时间范围和刷新按钮
- 每张卡片显示：
  - 标题
  - 来源
  - 更新时间
  - 缩略图
  - 简要说明
  - 查看原始数据
  - 查看交互图

当前首页模板：

- `web/templates/dashboard.html`

### 5.2 交互图

交互图必须满足：

- 单独打开时是大图
- 填充浏览器视口
- 随窗口变化自动重绘
- 保留 Plotly 工具栏
- 可以下载图片
- 可以缩放、平移、框选

当前交互图的排版要求：

- 纵轴标题字号更大
- 坐标刻度字号更大
- 时间轴字号更大
- 时间轴使用季度刻度
- 时间轴标签竖排
- 底部留白要足够，避免被裁掉

当前交互图 `html` 文件每次运行都必须重新写入。

这是一个强规则：

- 数据库可以尽量只请求最新增量
- 但 `html` 交互图必须每次重写

当前实现中，`html` 文件会写入生成时间标记：

- `<meta name="generated-at" ...>`
- `<!-- generated-at: ... -->`

用于确认该交互图是不是本次刚生成的。

### 5.3 原始数据页

每张卡片必须支持：

- 查看原始数据页
- 下载 Excel

当前页面：

- `web/templates/data.html`

下载规则：

- 下载格式是 `.xlsx`
- 不再使用 `.csv`

---

## 6. 配置系统

### 6.1 MCP 服务配置

文件：

- `config/mcp_servers.toml`

结构：

```toml
[server-name]
url = "https://..."
auth_token = "..."
```

### 6.2 主配置

文件：

- `config/indicators.yaml`

承载：

- `usd-liquidity` 模块主体

### 6.3 模块扩展配置

目录：

- `config/modules/`

当前已有：

- `config/modules/commodity_review.yaml`

规则：

- 先读 `config/indicators.yaml`
- 再把 `config/modules/*.yaml` 合并到 `themes`

新增模块时建议：

- 新建一个独立的 `config/modules/<module>.yaml`
- 不要把所有模块塞回一个大文件

### 6.4 主题命名

当前 `config.py` 中的默认模块标题映射包含：

- `usd-liquidity -> 流动性面板`
- `commodity-review -> 有色金属数据`

对应文件：

- `macro_replay/config.py`

---

## 7. 数据库设计

数据库文件：

- `data/replay.duckdb`

定义文件：

- `macro_replay/db.py`

主要表：

- `observations`
- `charts`
- `sources`
- `series`
- `raw_payloads`

### 7.1 observations

关键字段：

- `series_id`
- `indicator_id`
- `source_id`
- `obs_time`
- `value`
- `unit`
- `extra`

用途：

- 存所有原始和派生观测值

### 7.2 charts

关键字段：

- `indicator_id`
- `html_path`
- `image_path`
- `rendered_at`
- `source_summary`

用途：

- 首页卡片读取图表路径
- “查看交互图”读取 `html_path`
- 缩略图读取 `image_path`

### 7.3 当前最新数据库写入策略

这是本次更新后的重要规则：

- 普通时间序列：
  - 数据库尽量只请求和替换最新增量
  - 历史旧数据继续保留
- 图表生成：
  - 从数据库读取全量历史来作图
  - 每次都重写 `html`

实现方式：

- 新增了按 `series_id` 读取最新时间的逻辑
- 新增了“只替换某个时间点之后数据”的逻辑

对应函数在：

- `macro_replay/db.py`
  - `latest_observation_time`
  - `replace_observations_since`

---

## 8. 数据源架构

当前主要分三类：

- FRED
- iFind EDB
- iFind Stock

### 8.1 FRED

文件：

- `macro_replay/sources/fred.py`

特点：

- 直接请求 CSV URL
- 解析成 DataFrame
- 支持从指定开始日期截取
- SSL 异常时回退到 `verify=False`

### 8.2 iFind EDB

文件：

- `macro_replay/sources/ifind.py`

支持两类：

- `ifind`
  - 解析结构化返回
- `ifind-edb-table`
  - 从 markdown table 解析

### 8.3 iFind Stock

文件同样在：

- `macro_replay/sources/ifind.py`

主要用途：

- 期货合约级历史价格
- 如铜的 SHFE / COMEX 期现结构

### 8.4 非常重要的实际经验

iFind MCP 这一层并不总是“严格按代码查”，很多时候更像自然语言代理。

这会带来几个现实问题：

- 同一个 query 可能误匹配到别的指标
- 长 query 比短 query 更容易跑偏
- EDB 指标名相似时可能串品种
- 库存、LME 远月、某些特殊口径容易不稳定

因此生产规则必须是：

- 命不中预期列名时宁可返回空，也不要画错图
- 对模糊匹配出来的结果保持怀疑
- 真正稳定的口径优先用精确代码或官方 URL

---

## 9. 已实现模块与指标状态

### 9.1 流动性面板

当前主要包含：

- 联邦基金利率
- SOFR
- 10Y 国债收益率
- 10Y TIPS
- Breakeven
- 美元指数
- Fed Balance Sheet
- TGA
- RRP
- 银行准备金
- 货币市场基金
- SOFR-OIS spread
- 联邦财政赤字
- 赤字/GDP
- 10Y-2Y 利差
- HY spread
- RRP 其他项

其中关于财政赤字的最新要求是：

- `usd-fiscal-deficit`
- `usd-fiscal-deficit-gdp`

这两个当前主源已经改成 `FRED`，不再优先走 `iFind`。

原因：

- iFind 当前拿到的是更短、偏预测口径的数据
- 用户要求恢复更长历史
- 现已切回 FRED，并且当前数据库中时间范围恢复到 `2015-12-31` 起

### 9.2 有色金属数据

当前模块名称固定为：

- `有色金属数据`

其中铜相关当前状态：

- 已稳定完成：
  - 铜 SHFE 期现结构
  - 铜 COMEX 期现结构
- 暂未稳定落地：
  - 铜 LME 期现结构
  - LME 15M-3M
  - COMEX 15M-3M
  - 铜库存
  - 铜虚实比
  - 铜跨市套利
  - 铜进口盈亏

原因不是前端问题，而是数据源稳定性和可用性不足。

### 9.3 铝

铝相关代码和配置已删除，不属于当前版本。

---

## 10. 图表系统

文件：

- `macro_replay/charts.py`

支持类型：

- `line`
- `term_structure`
- `multi_axis_line`

### 10.1 缩略图规则

- 输出为 `png`
- 首页卡片展示用
- 尺寸较小

### 10.2 交互图规则

- 输出为 `html`
- 独立全屏展示
- 使用 Plotly
- 每次运行都要重写
- 必须保留工具栏

### 10.3 当前交互图最新样式要求

- 标题更大
- 纵轴标题更大
- y 轴刻度更大
- 时间轴刻度更大
- 日期型图表使用季度刻度
- 日期标签竖排
- 增加底部边距避免裁切
- 保持窗口自适应

### 10.4 旧 HTML 升级脚本

文件：

- `scripts/upgrade_chart_html.py`

用途：

- 把历史遗留的小尺寸 `html` 图升级成全屏逻辑
- 补工具栏
- 补全屏 resize

---

## 11. 主处理流程

文件：

- `macro_replay/pipeline.py`

### 11.1 普通时间序列

流程：

1. 读取指标配置
2. 对每个 `series` 计算应抓取的开始时间
3. 尽量只抓最新增量
4. 写回数据库
5. 再从数据库读取全量序列
6. 合并、计算、重采样
7. 生成 `png` 和 `html`
8. 写回 `charts` 表

### 11.2 Term Structure

这是特殊路径，不走普通时序逻辑。

当前已实现的重点是铜：

- SHFE 铜期现结构
- COMEX 铜期现结构

实现逻辑：

1. 动态生成候选合约代码
2. 批量向 iFind Stock 查询历史收盘价
3. 用最新交易日判断当前哪些合约仍有效
4. 取几个快照时点
5. 按交割月排序
6. 画期现结构折线

这条逻辑用于应对“合约会滚动更新，不想手工维护”的需求。

---

## 12. 铜期现结构的具体实现经验

这是当前工程里最重要的特殊业务实现之一。

### 12.1 SHFE 铜

代码模式：

- `CU2605.SHF`
- `CU2606.SHF`

规则：

- 动态生成未来若干月合约代码
- 查最新是否还有收盘价
- 有则保留，无则视为已到期或未挂牌

### 12.2 COMEX 铜

当前采用真实合约代码，而不是简单月度占位代码。

类似：

- `@HG26J.CMX`
- `@HG26K.CMX`
- `@HG26M.CMX`

月份字母规则：

- `FGHJKMNQUVXZ`

### 12.3 LME 铜

LME 相关目前未稳定完成，已确认过部分代码线索，但历史行情稳定性不足。

---

## 13. 服务与页面文件

### 13.1 Web 服务

文件：

- `web/server.py`

当前主要路由：

- `/`
- `/config`
- `/refresh-theme`
- `/data/{indicator_id}`
- `/data/{indicator_id}.xlsx`
- `/charts/...`

### 13.2 当前服务稳定性处理

最近新增的重要防护：

- 当数据库被后台进程暂时占用时，服务尽量不要直接 500
- 读取卡片和原始数据时，优先使用 `read_only=True`
- 读失败时尽量回退为空，而不是整个页面炸掉

---

## 14. 当前已知坑位

### 14.1 DuckDB 文件占用

这是当前工程最常见的问题之一。

现象：

- `Internal Server Error`
- 后台脚本重画图时首页突然 500

原因：

- 某个 Python 进程占住了 `replay.duckdb`

处理原则：

- 尽量使用只读连接读取页面
- 后台批量脚本不要长期占库
- 必要时结束残留进程

### 14.2 使用了错误 Python

现象：

- `No module named uvicorn`

原因：

- 用系统 Python 启动了服务，而不是 `.venv-win`

规则：

- 当前项目必须优先用 `.venv-win`

### 14.3 旧交互图缓存

现象：

- 明明改了图，但浏览器还显示旧效果

解决：

- 首页交互图链接附带 cache token
- 必要时重新点开或强刷

### 14.4 MCP 模糊匹配不可靠

现象：

- 问一个指标，返回另一个品种
- 问一个代码，跳到完全无关证券

规则：

- 对模糊结果保持怀疑
- 有精确代码时优先用精确代码
- 命不中就返回空，不要画错图

---

## 15. macOS 迁移要求

如果要迁移到 macOS，需要复刻以下核心能力，而不是只把 Python 文件拷过去：

### 15.1 必须保持不变的产品逻辑

- 总面板名字：`复盘面板`
- 模块刷新是网页内独立触发
- 模块时间设置要持久化
- 首页是 `png` 缩略图
- 交互图是独立 `html`
- 原始数据支持 Excel 下载

### 15.2 必须保持不变的技术逻辑

- DuckDB 作为主库存储
- Plotly 作为图表库
- FRED 与 iFind 作为核心数据源
- term structure 使用动态合约发现机制
- 普通时序使用“增量抓数 + 全量重写 html”

### 15.3 推荐迁移步骤

1. 创建新的 macOS 虚拟环境
2. 安装所有依赖
3. 复制配置目录
4. 复制或重建 `mcp_servers.toml`
5. 启动 FastAPI 服务
6. 验证首页、原始数据页、Excel 下载、交互图
7. 验证模块刷新与时间持久化

---

## 16. 给任何 AI 的实现任务定义

如果把这个工程交给另一个 AI，应该直接给它下面这类任务描述：

1. 用 Python 3.11、DuckDB、FastAPI、Plotly、Pandas 重建一个“复盘面板”系统。
2. 系统应支持多个主题模块，当前至少包含“流动性面板”和“有色金属数据”。
3. 配置系统分为：
   - `config/indicators.yaml`
   - `config/modules/*.yaml`
   - `config/mcp_servers.toml`
   - `config/theme_settings.toml`
4. 首页按模块展示卡片，每个模块有：
   - 开始时间
   - 结束时间
   - 刷新数据按钮
5. 点击刷新数据后，只刷新该模块，并持久化时间设置。
6. 首页使用 `png` 缩略图。
7. 点击“查看交互图”打开全屏 `html` 图。
8. 点击“查看原始数据”打开表格页，并支持下载 Excel。
9. 普通时间序列使用“数据库增量抓取 + 每次重写 html”的策略。
10. term structure 使用动态合约发现逻辑，不手工维护每月合约清单。
11. 对 iFind MCP 的模糊匹配结果要加防错保护，宁可空白，不要错图。
12. 当前财政赤字两张图必须使用 FRED，而不是 iFind。

---

## 17. 当前关键文件清单

以下路径全部是相对 `<PROJECT_ROOT>` 的相对路径：

- `macro_replay/config.py`
- `macro_replay/db.py`
- `macro_replay/pipeline.py`
- `macro_replay/charts.py`
- `macro_replay/sources/ifind.py`
- `macro_replay/sources/fred.py`
- `web/server.py`
- `web/templates/dashboard.html`
- `web/templates/data.html`
- `config/indicators.yaml`
- `config/modules/commodity_review.yaml`
- `config/theme_settings.toml`
- `docs/project_rebuild_spec_zh.md`

---

## 18. 当前版本结论

当前这份文档已经同步到以下最新状态：

- 模块化结构已落地
- 独立模块刷新已落地
- 模块时间持久化已落地
- 原始数据下载已切换为 Excel
- 首页缩略图使用图片而不是嵌入 html
- 交互图使用全屏大图逻辑
- 交互图每次运行都重写
- 普通时序已改成增量抓数
- 财政赤字两张图已切换为 FRED
- 铝相关逻辑已移除

如果以后再发生结构级变更，优先继续维护这份文档，而不是再散落在多个 `.md` 文件里。
