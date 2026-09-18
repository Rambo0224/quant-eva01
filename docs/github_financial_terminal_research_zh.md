# GitHub 开源金融终端对标笔记

更新日期：2026-04-26

这份笔记不是泛泛罗列“有哪些项目”，而是站在当前仓库的实际状态上，回答一个更重要的问题：

**哪些开源金融终端里的设计和组件，适合拿到本项目里复用。**

当前仓库已经有这些基础：

- 本地 DuckDB 存储
- `macro_replay/sources/` 数据源适配
- `app/services/` 服务层雏形
- `datahub/registry/` 的 `dataset/view/workspace` 抽象雏形
- Streamlit 终端页 `macro_replay/terminal_app.py`
- FastAPI Web 服务 `web/server.py`

所以我们不是要照抄一个“Bloomberg 克隆”，而是要借鉴成熟项目里**最值钱的结构设计、扩展方式、交互模式和可复用组件**。

---

## 1. 重点看的项目

### 1.1 OpenBB

- 仓库：<https://github.com/OpenBB-finance/OpenBB>
- 相关仓库：<https://github.com/OpenBB-finance/backends-for-openbb>
- 相关仓库：<https://github.com/OpenBB-finance/agents-for-openbb>

为什么值得看：

- 它不是单一 UI，而是把金融数据平台拆成了“数据底座 + 多种消费端”
- 强调 `connect once, consume everywhere`
- 有独立的“自定义数据后端接入模板”
- 有独立的“Agent/AI 接入模板”

最值得借的点：

1. **数据层与展示层彻底解耦**
   你的架构文档已经在往这个方向走，OpenBB 的做法说明这条路是对的。数据接入、标准化、对外输出应该是稳定接口，UI 只是其中一个消费端。

2. **标准化后端契约**
   `backends-for-openbb` 的核心思想不是 UI，而是：
   - 每个数据应用有固定 JSON 输出
   - 每类 widget 有固定元信息定义
   - 可以被不同前端统一消费

   对当前项目的启发是：
   - `datahub/registry/models.py` 不应该只停留在 dataclass
   - 应该逐步补成“可对外描述”的 registry 协议
   - 例如 dataset/view/workspace 最终都能序列化成稳定 JSON

3. **把 AI/Agent 当成终端插件，而不是核心主流程**
   `agents-for-openbb` 展示的是“外挂式能力”：
   - 图表 agent
   - 表格 agent
   - PDF agent
   - dashboard widget agent

   对你项目的启发是：
   - 先把 `dataset/view/workspace` 打稳
   - 再给终端加“自然语言找图、解释指标、生成晨会页”这类 agent 功能
   - 不要反过来先做 agent，再回头补数据结构

适合你现在就借的内容：

- registry 输出协议
- widget/view 元信息定义
- 面向多前端的服务层
- agent 作为附加层的接入方式

不建议现在直接照搬的内容：

- OpenBB 的完整平台规模过大
- 全量 provider 体系、Workspace 兼容层、企业化能力太重

---

### 1.2 FinceptTerminal

- 仓库：<https://github.com/Fincept-Corporation/FinceptTerminal>

为什么值得看：

- 它代表“功能想象力上限”很高的金融终端
- 模块覆盖范围广：研究、组合、新闻、AI、节点编辑器、数据连接器
- 它的 README 很像一份“终端产品能力地图”

最值得借的不是代码，而是**产品分区方式**：

- Equity Research
- Portfolio
- News
- Node Editor
- Data Sources
- AI Agents

对当前项目的启发：

你现在的主题主要还是“宏观专题页”，下一步可以逐渐把终端信息架构从“主题集合”扩成“能力分区”：

- `Macro`
- `Rates & Liquidity`
- `Commodities`
- `FX`
- `Credit`
- `Portfolio`
- `News`
- `Workspace`

这样以后新增能力时，不会所有东西都挤在一个 dashboard 里。

还值得借的点：

1. **节点式分析思路**
   它的 Node Editor 说明一个重要方向：金融终端里“变换链”本身可以成为一等公民。

   对应你现在项目里，就是把这些能力做成可组合对象，而不只是页面按钮：
   - `rebase`
   - `pct_change`
   - `rolling_mean`
   - `spread`
   - `ratio`
   - `join`
   - `align_frequency`

2. **数据连接器是单独能力域**
   当前仓库的数据源适配还比较贴近 pipeline。后面可以把 connector 独立成显式概念：
   - source adapter
   - credential config
   - health check
   - refresh job

不建议拿来照搬的部分：

- 原生 C++/Qt 桌面方案
- 大量交易/券商/AI agent 能力
- AGPL/商业双许可代码直接复用

更适合把它当成：

**产品能力蓝图，而不是实现模板。**

---

### 1.3 Ghostfolio / Rotki / Wealthfolio

- Ghostfolio：<https://github.com/ghostfolio/ghostfolio>
- Rotki：<https://github.com/rotki/rotki>
- Wealthfolio：<https://github.com/afadil/wealthfolio>

这三类项目虽然不完全是“金融数据终端”，但非常值得参考，因为它们在三个方向上做得很成熟：

1. **本地优先或自托管**
2. **工作区/持久化状态**
3. **插件或扩展机制**

最值得借的点如下。

#### A. 工作区与持久化状态

你的文档已经提出 `workspace`，但现在实现还比较薄，`datahub/registry/loader.py` 里还是从 theme 推导 workspace。

这些项目给出的启发是：

- 工作区不能只是“某个主题包含哪些图”
- 它还应该持久化：
  - 时间范围
  - 视图布局
  - 选中系列
  - 变换参数
  - 排序/筛选条件
  - 备注

这会让你的终端从“看板”变成“真正可复盘的工作台”。

#### B. Secrets 与本地配置管理

Wealthfolio 特别强调：

- 本地数据
- 敏感凭证安全存放
- 扩展能力的权限控制

对当前项目的启发很直接：

- `config/mcp_servers.toml` 适合作为起点，但长期不够
- 后面可以把数据源凭证管理升级为：
  - source registry
  - credential provider
  - 健康检查/连接测试
  - UI 中的连接状态页

这跟你已有的 [`web/templates/servers.html`](/Users/heshuyang/Documents/数据看板/web/templates/servers.html) 能自然接上。

#### C. Addon / Extension 机制

Wealthfolio 的 addon system 很值得借鉴，原因不是它的桌面壳，而是它把“扩展”做成了正式能力：

- 新页面
- 新导航入口
- 新数据能力
- 事件监听
- 安全边界

对你项目最适合的最小版落地是：

- 先不要做完整插件市场
- 先定义 `datahub/adapters/` 的稳定接口
- 再定义 `config/views/` 和 `config/workspaces/` 的外部扩展方式
- 让新模块可以“加 YAML/加 adapter/加 transform”完成扩展

---

### 1.4 OpenStock

- 仓库：<https://github.com/Open-Dev-Society/OpenStock>

为什么值得看：

- 它不是大而全平台，而是一个很现代的 Web 金融终端壳
- 信息架构和交互层更贴近普通用户的第一感受
- 有几个对你非常实用的 UI 点子

最值得借的点：

1. **Command Palette**
   OpenStock 用了 `cmdk` 做全局搜索和命令入口。

   对你项目非常适合，因为你已经在做 dataset/view/workspace 抽象。后面完全可以做：
   - 搜 `SOFR`
   - 搜 `美元流动性`
   - 搜 `铜`
   - 直接跳到 dataset / view / workspace

   这会比继续堆左侧栏更像“终端”。

2. **Watchlist / 收藏夹**
   你的终端如果后面加入“晨会页”“自选宏观指标”“本周重点图”，本质上就是 watchlist 的金融研究版。

3. **搜索优先，而不是导航优先**
   现代终端常见模式不是“先点五层菜单”，而是：
   - 搜索
   - 预览
   - 打开
   - 加入工作区

4. **轻量组件优先**
   OpenStock 很多页面是用标准 UI 壳把外部数据组件串起来的。这个思路对你有价值：
   - 不要每个新功能都先设计一套大页面
   - 可以先把 dataset 卡片、比较视图、数据表、搜索结果页这些基础壳搭稳

注意点：

- OpenStock 依赖 Finnhub 和 TradingView widgets
- 很多“好看”的部分来自外部嵌入组件
- 适合借 UI 结构，不适合直接搬核心数据方案

---

## 1.5 这些“小项目”反而更能给 idea

下面这些项目不一定是著名“金融终端”，但很值得看，因为它们提供的是**产品灵感和交互想法**。

### A. TradeNote

- 仓库：<https://github.com/Eleven-Trading/TradeNote>

TradeNote 是一个交易日志工具，不是终端，但它提醒了一个很重要的点：

**用户不只想看数据，还想记住自己为什么看它。**

它里面最值得借的不是“交易记录表”，而是这些思路：

- 给一笔交易打标签
- 记录错误类型
- 绑定截图
- 回看并总结模式

翻译到你的终端里，可以变成：

- 给某张图打标签
- 给某个时间区间写判断
- 给一个 workspace 记“这周看它是因为啥”
- 给指标绑定“风险提示/误读提醒”

也就是说，你的终端可以不只是“看图”，还可以是：

**研究判断的外部记忆。**

---

### B. TradeLens

- 仓库：<https://github.com/mingi3314/tradelens>

TradeLens 的有趣点是它把交易数据转成 Obsidian 笔记。

这给你的启发非常强：

- 终端不一定只输出图表
- 终端也可以输出 Markdown 研究卡片
- 图、数据、结论、备注可以一起归档

你这边很适合长出两个功能：

1. `Export to Markdown Note`
   把当前 view/workspace 导出成研究笔记

2. `Daily Brief Builder`
   自动把今天刷新过的重点图，生成一页晨会 Markdown

这个思路和你“复盘数据库”的定位特别搭。

---

### C. Flowsurface

- 仓库：<https://github.com/flowsurface-rs/flowsurface>

这是个偏 crypto 的图形终端，但它给了几个很强的交互灵感：

- 多面板联动
- pane linking
- heatmap
- footprint
- time & sales
- DOM / ladder

你不一定要上订单流，但它背后的交互思想很值得借：

1. **多窗格联动**
   - 切一个日期，所有图同步
   - 切一个标的，同组图同步

2. **面板分组**
   - A 组看美元流动性
   - B 组看铜产业链
   - C 组看信用风险

3. **主图 + 辅助图**
   - 主图看价格/利率
   - 副图看变化率、z-score、spread、相关性

你现在的终端非常适合加入“pane group”的概念，这会一下子从看板提升到终端。

---

### D. OpenAlgo

- 仓库：<https://github.com/marketcalls/openalgo>

OpenAlgo 很值得看的不是交易本身，而是它把一个项目拆成了：

- Core
- 数据管理平台
- SDK
- Excel 插件
- MCP Server
- 浏览器插件
- Web Portal

这会给你一个非常实用的战略启发：

**你的项目未来不必只有一个 Streamlit 页面。**

它完全可以是一个小生态：

- `macro-replay cli`
- 浏览器终端
- 数据刷新服务
- MCP/Agent 入口
- Markdown/Excel 导出
- 研究模板生成器

这不是“做大做全”，而是提早把边界想清楚。

---

### E. Freeboard

- 仓库：<https://github.com/Freeboard/freeboard>

它不是金融项目，但它的架构非常值得借：

- datasource plugin
- widget plugin
- 浏览器里直接拼 dashboard

这和你现在在做的东西天然契合。

对你项目最值得借的，是这个组合：

- `dataset` 像 datasource
- `view` 像 widget
- `workspace` 像 dashboard layout

换句话说，你现在的 registry 三件套，其实正好可以朝一个成熟 dashboard engine 的方向生长。

---

### F. forex-centuries

- 仓库：<https://github.com/unbalancedparentheses/forex-centuries>

这个项目不是终端，而是长周期金融经济数据集工程。但它非常有价值，因为它把数据项目做得很“可审计”：

- 有构建流水线
- 有数据质量校验
- 有派生数据产物
- 有自动更新
- 有 notebook 探索

它给你的启发是：

**金融终端的可信度，很多时候来自数据工程质量，而不是 UI。**

你后面非常值得补这些概念：

- source provenance
- refresh log
- validation checks
- revision/backfill note
- dataset quality badge

例如在 dataset 详情页显示：

- 来源
- 最后刷新时间
- 最近修订日期
- 缺失率
- 可用区间
- 更新频率

这个东西会让终端很“专业”。

---

### G. FRED MCP Server

- 仓库：<https://github.com/stefanoamorelli/fred-mcp-server>

这个项目本身不复杂，但它点醒了一件事：

**海量指标的核心问题不是抓数，而是发现。**

它把 80 万级时间序列做成统一可访问入口，说明：

- 搜索
- 自动补全
- 语义发现
- 相关系列推荐

是金融终端里非常关键的一层。

你后面可以长出这些功能：

- 输入 `SOFR` 自动给出候选
- 看美债收益率时推荐 TIPS、OIS、联邦基金利率
- 看铜价时推荐库存、期限结构、美元指数

也就是：

**从“数据仓库”变成“研究发现引擎”。**

---

## 2. 组件级别最值得拿来用的东西

除了“整项目对标”，还有几类可以直接服务当前仓库的开源组件。

### 2.1 `lightweight-charts`

- 仓库：<https://github.com/tradingview/lightweight-charts>
- 协议：Apache-2.0

为什么重要：

- 这是一个非常适合金融时间序列的轻量高性能图表库
- 比“静态图片卡片”更像真正终端
- 适合多图并列、缩放、悬停、十字光标、金融时间轴

对你项目的价值非常高：

- 当前你大量使用 Plotly + 静态导出 + HTML 页面
- 但终端页真正需要的是“快速、轻、可比较、可同步缩放”的交互图

最适合的用法：

- 中短期用于浏览器终端的主图层
- 保留 Plotly 作为复杂图、导出图、探索性分析图
- `lightweight-charts` 负责日常浏览、对比、联动

建议接入位置：

- 若继续强化 FastAPI/Web 端：优先接到 `web/templates/` 新页面
- 若继续以 Streamlit 为主：先试 wrapper，验证交互收益

---

### 2.2 `streamlit-lightweight-charts`

- 仓库：<https://github.com/freyastreamlit/streamlit-lightweight-charts>

为什么重要：

- 你现在已经有 `macro_replay/terminal_app.py`
- 这是“最小改动就把终端图从静态/Plotly 拉向交易终端体验”的捷径

适合你当前阶段的价值：

- 不改大架构
- 不重写前端
- 先验证：
  - 放大/缩放体验
  - 多图性能
  - k 线/面积/基线图是否更合适

我会把它定义为：

**低风险 UI 试验件。**

---

### 2.3 `react-financial-charts`

- 仓库：<https://github.com/react-financial/react-financial-charts>

为什么值得记住：

- 如果未来从 Streamlit 迁到 React Web 终端，这个库很有价值
- 它有金融图表特有的东西：
  - candlestick
  - OHLC
  - 各类技术指标
  - drawing objects

对你当前项目是否立刻适合：

- 现在不适合马上接
- 但如果后面你把 Web 端升级为独立前端，它会比通用图表库更贴金融终端场景

---

## 3. 对当前仓库最值得落地的 8 个借鉴点

下面这 8 个点，是我认为最适合直接进入你路线图的。

### 3.1 真的把 `workspace` 做实

现状：

- `WorkspaceDefinition` 已存在
- 但目前还是从 theme 推导出来

建议：

- 在 `config/workspaces/` 放独立定义
- 保存布局、默认时间范围、常用 transform、默认比较对象
- 允许“一个 dataset 出现在多个 workspace”

对应灵感来源：

- Ghostfolio
- Wealthfolio
- OpenStock 的 watchlist / personalized pages

---

### 3.2 把 `view` 从“默认图”升级成“图表协议”

现状：

- `datahub/registry/loader.py` 里 `view` 基本是 dataset 的镜像

建议：

- 一个 dataset 可以有多个 view：
  - `raw`
  - `pct_change`
  - `rebase_compare`
  - `spread`
  - `term_structure`
  - `heatmap`
- view 要有独立配置文件，不再自动推导全部内容

对应灵感来源：

- OpenBB 的 widget 定义
- FinceptTerminal 的分析模块化思路

---

### 3.3 引入“连接器注册表”

现状：

- 数据源主要散在 `macro_replay/sources/` 和配置文件里

建议：

- 显式定义 `source registry`
- 每个 source 至少有：
  - `source_id`
  - `display_name`
  - `auth_type`
  - `healthcheck`
  - `refresh_capabilities`
  - `rate_limit_notes`

对应灵感来源：

- OpenBB provider/backend 思路
- FinceptTerminal 的 Data Sources 概念
- Wealthfolio 的 secrets / extension 管理

---

### 3.4 给终端加全局搜索 / Command Palette

现状：

- 现在更像浏览式看板

建议：

- 搜 dataset / view / workspace / source
- 支持关键词、标签、代码、主题检索
- 支持“加入对比”“加入工作区”“刷新该数据集”

对应灵感来源：

- OpenStock

这件事的性价比非常高，因为你已经有：

- `list_datasets()`
- `list_views()`
- `list_workspaces()`

也就是说，数据入口其实已经快够了，只差 UI 交互壳。

---

### 3.5 给变换链建立正式对象模型

现状：

- `datahub/transforms/common.py` 已有一组函数

建议：

- 不只保留函数调用
- 还要有变换定义对象：
  - `transform_id`
  - `input_fields`
  - `parameters`
  - `output_schema`

这样以后才能支持：

- view 配置化
- 节点式分析
- 结果缓存
- 调试和回放

对应灵感来源：

- FinceptTerminal Node Editor
- OpenBB 的模块化数据消费方式

---

### 3.6 把图表渲染拆成“两层”

建议的图表策略：

1. **分析/导出层**
   继续使用 Plotly

2. **终端交互层**
   尝试 `lightweight-charts`

这样做的好处：

- 不推翻现有图表生成能力
- 同时提升终端交互体验

---

### 3.7 引入任务状态与刷新日志

现状：

- 现在刷新动作能做，但任务层还是比较轻

建议：

- 为 dataset/theme/workspace 刷新增加 job 记录
- 最少记录：
  - job_id
  - target_type
  - target_id
  - started_at
  - finished_at
  - status
  - row_count
  - error_message

对应灵感来源：

- OpenBB 后端化思路
- Ghostfolio/Wealthfolio 的持续运行产品心态

这对“本地终端产品化”很重要，因为一旦数据越来越多，刷新可观测性会变成刚需。

---

### 3.8 预留 addon / external module 能力

建议不是现在就做插件市场，而是先把扩展边界设计出来：

- 增加一个 dataset
- 增加一个 transform
- 增加一个 view
- 增加一个 workspace

都应该有稳定入口，而不是改三四个内部文件。

对应灵感来源：

- Wealthfolio addon system
- OpenBB backend/agent template

---

## 4. 按优先级给你的建议

### 4.1 第一优先级：马上值得做

1. 把 `workspace` 配置独立出来，不再完全从 theme 推导
2. 把 `view` 独立配置化，允许一份 dataset 对应多个 view
3. 加全局搜索/Command Palette
4. 给刷新流程补 job 状态

这四件事会直接把项目从“图表页集合”推向“终端底座”。

### 4.2 第二优先级：接下来很值

1. 引入 `lightweight-charts` 做终端交互图
2. 补 source registry 和连接状态页
3. 扩展 transform 对象模型
4. 加收藏/晨会页/自选工作区

### 4.3 第三优先级：先记着，不急着上

1. AI agent 深度集成
2. 节点式分析编辑器
3. 多账户/组合/交易执行
4. 原生桌面客户端

这些方向都可以做，但不应该抢在底层抽象前面。

---

## 4.4 比代码更重要的一批 idea

如果你的瓶颈不只是代码和功能，而是“终端到底还能长成什么样”，那下面这些 idea 比加十个按钮更值钱。

### Idea 1. 晨会模式

不是普通 dashboard，而是一个每天自动生成的“今天先看什么”页：

- 今日有更新的数据集
- 最近一周变化最大的系列
- 超过阈值的指标
- 你收藏的工作区摘要
- 自动生成三条观察备注草稿

### Idea 2. 研究路径模式

用户不是直接打开图，而是进入一条“研究路径”：

- 美元流动性
- 美国通胀
- 铜供需
- 中国信用脉冲

每条路径里不只有图，还包括：

- 推荐先后顺序
- 必看联动指标
- 常见误判
- 研究问题模板

### Idea 3. 图表附注层

允许用户在某张图上留下注释：

- 这个拐点为什么重要
- 当时发生了什么政策/事件
- 以后怎么看类似情形

这会让终端逐渐积累成你的研究资产，而不只是数据播放器。

### Idea 4. 一键生成研究卡片

从某个 dataset/view/workspace 自动生成：

- 标题
- 时间范围
- 当前值
- 过去 1 周 / 1 月 / 1 年变化
- 同类指标对比
- 一段自动摘要

可导出为 Markdown、HTML、PDF。

### Idea 5. 联动探索模式

当用户看某个图时，右侧不是静态信息，而是“下一步你可能想看”：

- 同频率指标
- 同主题指标
- 领先/滞后关系指标
- 可做 spread 的指标
- 可做 rebase compare 的指标

### Idea 6. 工作区像“桌面”而不是“页面”

工作区里不只是图卡片，而是可摆放对象：

- 图
- 数据表
- 注释
- 待办
- 研究问题
- 刷新状态

### Idea 7. 研究记忆系统

把终端当成“金融版第二大脑”：

- 每次查看记录
- 收藏原因
- 历史判断
- 过去判断的复盘结果

### Idea 8. 数据可信度可视化

每个 dataset 不只显示值，还显示：

- 数据新鲜度
- 缺口
- 来源可信度
- 是否有修订历史
- 是否来自手动导入

### Idea 9. 问题驱动导航

不要只按资产分类导航，也可以按问题导航：

- “现在美元是松还是紧？”
- “通胀回落是否结束？”
- “铜价上涨更像需求还是供给？”
- “风险偏好在改善还是恶化？”

每个问题背后绑定一组 workspace/view。

### Idea 10. 图谱式发现

把 dataset 做成网络：

- 主题关系
- 来源关系
- 高频/低频关系
- 领先/滞后关系
- 常用组合关系

这样用户不是靠菜单找数据，而是靠关系发现数据。

---

## 5. 许可与复用边界

这里需要非常务实。

### 可以优先考虑直接复用或深度参考的

- `lightweight-charts`：Apache-2.0
- `backends-for-openbb`：MIT
- `agents-for-openbb`：MIT

这几类更适合直接借结构、协议和部分实现思路。

### 更适合借产品设计，不适合随意复制代码的

- OpenBB 主仓库
- FinceptTerminal
- Ghostfolio
- Rotki
- Wealthfolio
- OpenStock

原因不是它们没价值，而是：

- 规模太大
- 技术栈差异明显
- 多数是 AGPL 或带商业限制的路线

所以更合理的做法是：

**借架构、借交互、借概念，不轻易搬核心代码。**

---

## 6. 结合当前代码，建议的最短落地路线

如果只选一个“最现实、最有收益”的 2 周路线，我会建议：

1. 在 `config/views/`、`config/workspaces/` 建独立配置
2. 改 `datahub/registry/loader.py`，停止把 view/workspace 完全从旧 indicator/theme 推导
3. 给 `app/services/` 增加统一搜索入口
4. 在 `macro_replay/terminal_app.py` 里加搜索/快速打开
5. 试接一个 `lightweight-charts` 交互图页面
6. 给刷新流程落一张 job 表

这条路线同时吸收了：

- OpenBB 的 registry / contract 思路
- OpenStock 的终端交互方式
- Wealthfolio / Ghostfolio 的 workspace 与状态持久化经验

而且最重要的是：

**它和你当前仓库的结构是连续的，不需要推倒重来。**
