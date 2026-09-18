# 金融数据终端实施清单

本文档是在 [financial_terminal_architecture_zh.md](/Users/heshuyang/Documents/数据看板/docs/financial_terminal_architecture_zh.md) 的基础上继续往下落一层。

目标不是再讲一遍架构理念，而是回答下面这些更具体的问题：

- 第一阶段先创建哪些目录和文件
- 当前 `indicator` 配置应该怎么拆
- `dataset registry` 第一版先长什么样
- 现有代码哪些先搬，哪些先保留
- 哪些事情本周就能开始做

本文档默认遵循一个原则：

**不推倒重来，优先渐进迁移。**

也就是说：

- 先让新结构能够与旧结构并存
- 再逐步把现有能力搬过去
- 最后才考虑去掉旧实现

---

## 1. 实施目标

当前项目已经能抓数、存数、生成图和在浏览器查看。

下一阶段不是“让它从 0 变 1”，而是让它从“能用的面板”变成“可持续扩展的终端底座”。

实施目标分成四个层次：

1. 让数据对象更清楚
   从 `indicator` 走向 `dataset + view`

2. 让扩展方式更清楚
   新增数据源、新增图、新增工作区都能按固定位置添加

3. 让服务边界更清楚
   把 UI 逻辑和数据逻辑逐步拆开

4. 让后续前端升级更轻
   未来如果从 Streamlit 迁移，不必重做全部业务逻辑

此外建议再加一条实施约束：

**Excel 只作为补充数据源，不作为终端的主要研究界面。**

这意味着：

- Excel 可以用于导入和手工补数
- 但研究、对比、工作区、备注、导出都应围绕系统内部的 dataset / view / workspace 展开
- 一旦数据导入完成，运行时真相应落在 DuckDB 和 registry 中，而不是留在原始文件中

---

## 2. 第一阶段建议新增的目录与文件

第一阶段不要做大规模迁移，只需要先把“新世界”的骨架放进去。

建议新增下面这些目录：

```text
<PROJECT_ROOT>/
  app/
    services/
  datahub/
    adapters/
    registry/
    storage/
    transforms/
    jobs/
  config/
    datasets/
    views/
    workspaces/
  tests/
    registry/
    transforms/
```

### 2.1 第一阶段最少要建的文件

建议第一批先加这些文件：

```text
app/services/dataset_service.py
app/services/view_service.py
app/services/workspace_service.py

datahub/registry/models.py
datahub/registry/loader.py
datahub/storage/registry_db.py
datahub/transforms/base.py
datahub/transforms/common.py
datahub/jobs/refresh.py

config/datasets/README.md
config/views/README.md
config/workspaces/README.md
```

### 2.2 为什么先建这些

理由很简单：

- `registry` 负责把新概念站住
- `storage` 负责给 registry 落库
- `services` 负责给当前 UI 提供新入口
- `transforms` 负责把分析能力独立出来
- `jobs` 负责以后统一刷新和状态追踪

现在不需要一开始就加很多文件，关键是先把职责位置固定住。

---

## 3. 第一阶段目录职责定义

### 3.1 `app/services/`

这里负责提供“页面和 API 能直接调用的业务入口”。

建议职责：

- `dataset_service.py`
  - 列出数据集
  - 读取数据集元信息
  - 获取某个数据集的标准化数据

- `view_service.py`
  - 列出视图
  - 根据 `view_id` 组装可画图的数据
  - 管理视图和图表模板的映射

- `workspace_service.py`
  - 列出工作区
  - 读取某个工作区的布局定义
  - 保存工作区（后续）

当前的 [dashboard_service.py](/Users/heshuyang/Documents/数据看板/macro_replay/dashboard_service.py) 可以暂时继续存在，但后续要逐步把真正的业务能力搬到这里。

### 3.2 `datahub/adapters/`

这里未来放标准化后的数据源适配器。

第一阶段可以先不急着搬代码，但要先确立方向：

- `ifind.py`
- `fred.py`
- `manual_excel.py`

当前 [macro_replay/sources/](/Users/heshuyang/Documents/数据看板/macro_replay/sources) 先保留，后续逐步搬迁。

这里的 `manual_excel.py` 建议明确定位为：

- 一个正式的数据源适配器
- 负责读取 Excel 并映射成标准化 dataset
- 负责记录导入元信息和可追溯信息

而不是：

- 让用户长期围绕 Excel 直接做研究
- 让 Excel 文件承载工作区和图表配置
- 让原始表格成为系统内部的最终真相

换句话说，Excel 在实施上属于 `ingestion path`，不属于 `research runtime`。

### 3.2.1 `manual_excel` 第一版建议职责

第一版不需要把 Excel 做得太复杂，但建议至少支持下面这些能力：

- 指定文件路径
- 指定 sheet 名
- 指定日期列、值列、可选标签列
- 指定单位、频率、时区
- 指定目标 `dataset_id`
- 指定导入模式：追加或覆盖
- 记录导入备注

建议输入配置至少包含：

- `source_id`
- `file_path`
- `sheet_name`
- `date_column`
- `value_column`
- `series_name` 或 `dataset_id`
- `unit`
- `frequency`
- `timezone`
- `import_mode`
- `note`

建议输出结果至少包含：

- 标准化后的 observation rows
- 导入批次号
- 原文件路径
- 文件修改时间
- 导入时间
- 导入条数
- 覆盖的日期范围

这样后面即使你是“手工补数据”，系统也知道：

- 这批数据从哪里来
- 什么时候导入
- 改动了什么范围
- 是否覆盖了旧数据

### 3.3 `datahub/registry/`

这是第一阶段最重要的目录。

它负责：

- 定义 `DatasetDefinition`
- 定义 `ViewDefinition`
- 定义 `WorkspaceDefinition`
- 负责从 `config/datasets`、`config/views`、`config/workspaces` 加载配置

建议先做纯配置加载，不要一开始就掺太多业务逻辑。

如果后面接 `manual_excel`，也建议在 registry 中明确记录：

- 该 dataset 的 source 类型
- 是否为手工导入
- 上次导入时间
- 是否允许覆盖
- provenance / note

### 3.4 `datahub/storage/`

这里负责和 DuckDB 交互，但只管“新模型”的存取，不直接承载所有 pipeline 逻辑。

建议第一批先做：

- registry 元信息表初始化
- dataset registry 写入与读取
- view registry 写入与读取

### 3.5 `datahub/transforms/`

这里负责“分析能力”，不要再把变换塞回配置解析或图表函数里。

建议第一批只支持：

- `identity`
- `rebase`
- `pct_change`
- `rolling_mean`
- `spread`
- `ratio`

先从最常见、最稳定的能力开始。

### 3.6 `datahub/jobs/`

负责刷新任务的抽象。

第一阶段可以非常轻，只做：

- 刷新某个 dataset
- 刷新某个 theme/workspace
- 返回成功或失败

先不要急着上完整队列系统，接口统一比“功能炫”更重要。

---

## 4. 当前 `indicator` 配置怎么拆

这是最关键的迁移问题。

当前 `indicator` 同时描述了：

- 数据从哪里来
- 怎么取
- 怎么算
- 怎么画
- 属于哪个主题

下一版建议拆成三层。

### 4.1 旧结构

当前一个 `indicator` 里混合了这些内容：

- `source`
- `server`
- `tool`
- `arguments`
- `series`
- `calculation`
- `chart`
- `theme`

这种混合结构在 Excel 场景里尤其容易失控，因为文件路径、sheet、列映射、展示方式、主题归属很容易被写在同一处，后面越来越难维护。

这会导致：

- 复用困难
- 图表和数据绑死
- 页面结构绑进数据定义

### 4.2 新结构

建议拆成：

- `dataset`
  描述“数据是什么，怎么来，怎么存”

- `view`
  描述“怎么展示、怎么变换、怎么画”

- `workspace`
  描述“哪些 view 组成一个主题页”

### 4.3 拆分映射规则

可以先按下面规则拆。

#### `indicator -> dataset`

这些字段归入 `dataset`：

- `id`
- `title`
- `description`
- `source`
- `server`
- `tool`
- `series`
- `arguments`
- `theme` 中与数据本身相关的标签

#### `indicator -> view`

这些字段归入 `view`：

- `chart`
- `calculation`
- `chart.type`
- `chart.y_field`
- `chart.left_fields`
- `chart.right_fields`
- `chart.field_labels`

#### `indicator -> workspace`

这些字段归入 `workspace`：

- 原来的 `theme`
- 页面分组关系
- 卡片展示顺序

### 4.4 一个实际示例

原来可能是这样一个 `indicator`：

```yaml
id: usd-sofr
title: SOFR
theme: usd-liquidity
source: fred
series:
  - code: SOFR
chart:
  type: line
  y_field: SOFR
```

拆完可以变成：

`config/datasets/usd.sofr.daily.yaml`

```yaml
id: usd.sofr.daily
title: SOFR
description: Secured Overnight Financing Rate
source:
  type: fred
fetch:
  series:
    - code: SOFR
storage:
  frequency: daily
  unit: "%"
  revision_lookback_days: 30
tags:
  - usd
  - rates
  - liquidity
```

`config/views/usd.sofr.line.yaml`

```yaml
id: usd.sofr.line
title: SOFR
dataset_ids:
  - usd.sofr.daily
transform:
  - type: identity
chart:
  type: line
  y_field: SOFR
```

`config/workspaces/usd_liquidity.yaml`

```yaml
id: usd_liquidity
title: 美元流动性
views:
  - usd.sofr.line
```

---

## 5. `dataset registry` 第一版建议长什么样

第一版不要做太复杂。

核心目标只有两个：

- 能清楚列出系统里有哪些标准化数据对象
- 能把“数据定义”和“图表定义”分开

### 5.1 Python 模型建议

建议第一版先定义三个 dataclass。

`datahub/registry/models.py`

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DatasetDefinition:
    id: str
    title: str
    description: str
    source_type: str
    fetch: dict[str, Any]
    storage: dict[str, Any]
    tags: list[str] = field(default_factory=list)


@dataclass
class ViewDefinition:
    id: str
    title: str
    dataset_ids: list[str]
    transform: list[dict[str, Any]]
    chart: dict[str, Any]


@dataclass
class WorkspaceDefinition:
    id: str
    title: str
    views: list[str]
    layout: dict[str, Any] | None = None
```

### 5.2 DuckDB 中的 registry 表建议

第一版就先加三张元信息表即可：

- `dataset_registry`
- `view_registry`
- `workspace_registry`

建议字段尽量简单。

#### `dataset_registry`

- `dataset_id`
- `title`
- `description`
- `source_type`
- `tags_json`
- `definition_json`
- `updated_at`

#### `view_registry`

- `view_id`
- `title`
- `dataset_ids_json`
- `definition_json`
- `updated_at`

#### `workspace_registry`

- `workspace_id`
- `title`
- `views_json`
- `definition_json`
- `updated_at`

第一版不急着做大量拆表，直接存 `definition_json` 足够灵活。

### 5.3 为什么第一版先用 registry 表

因为后面你会很需要这些能力：

- 全局搜索
- 列出可用数据集
- 查哪些 view 在用某个 dataset
- 查某个 workspace 依赖哪些视图

如果没有 registry，后面所有查询都只能去翻 YAML 文件，会越来越痛苦。

---

## 6. 现有代码怎么迁移

不要试图一次搬空，建议按“外壳不动，内核逐步替换”的方式进行。

### 6.1 第一批先保留不动的代码

这些先不要碰太多：

- [macro_replay/streamlit_app.py](/Users/heshuyang/Documents/数据看板/macro_replay/streamlit_app.py)
- [macro_replay/webui.py](/Users/heshuyang/Documents/数据看板/macro_replay/webui.py)
- [scripts/run_dashboard.sh](/Users/heshuyang/Documents/数据看板/scripts/run_dashboard.sh)

原因：

- 它们已经能提供一个可访问的 UI 壳
- 当前改它们的收益不如先改数据模型高

### 6.2 第一批优先抽离的代码

这些最适合先拆：

- [macro_replay/config.py](/Users/heshuyang/Documents/数据看板/macro_replay/config.py)
  当前承担了太多配置入口职责，可逐步拆给 `datahub/registry/loader.py`

- [macro_replay/pipeline.py](/Users/heshuyang/Documents/数据看板/macro_replay/pipeline.py)
  当前混合了抓数、计算、写库、图表渲染、主题刷新，应逐步拆给：
  - `datahub/jobs/refresh.py`
  - `datahub/transforms/common.py`
  - `app/services/view_service.py`

- [macro_replay/dashboard_service.py](/Users/heshuyang/Documents/数据看板/macro_replay/dashboard_service.py)
  当前适合逐步演进为 UI 调用层，再把真实业务能力下沉到 `app/services/`

### 6.3 第一批不要急着迁的代码

这些可以暂时只做适配，不做重写：

- [macro_replay/charts.py](/Users/heshuyang/Documents/数据看板/macro_replay/charts.py)
- [macro_replay/sources/fred.py](/Users/heshuyang/Documents/数据看板/macro_replay/sources/fred.py)
- [macro_replay/sources/ifind.py](/Users/heshuyang/Documents/数据看板/macro_replay/sources/ifind.py)

理由：

- 它们已经是相对独立的功能模块
- 先通过包装复用，成本更低

---

## 7. 第一阶段建议落地的具体任务

下面这些任务是可以按周推进的。

### 任务 1：建立 registry 基础骨架

输出物：

- `datahub/registry/models.py`
- `datahub/registry/loader.py`
- `datahub/storage/registry_db.py`

完成标准：

- 能从 `config/datasets` 读取一个 dataset 定义
- 能从 `config/views` 读取一个 view 定义
- 能把它们写入 DuckDB registry 表

### 任务 2：给现有 indicator 做第一批映射

输出物：

- 一个迁移脚本，能把旧 `indicator` 转成新的 `dataset/view` 草稿

完成标准：

- 至少能覆盖最简单的单序列 line chart
- 不追求一次覆盖所有复杂商品结构图

### 任务 3：做一个新的 dataset service

输出物：

- `app/services/dataset_service.py`

完成标准：

- 能列出全部 dataset
- 能按 id 读取 dataset 定义
- 能按 id 返回标准化观测值

### 任务 4：做一个新的 view service

输出物：

- `app/services/view_service.py`

完成标准：

- 能根据 `view_id` 找到 dataset
- 能应用 transform
- 能产出 plot-ready dataframe

### 任务 5：让 Streamlit 先消费新 service

输出物：

- 在当前 Streamlit 页面里，挑一两个简单模块先走新 `dataset/view` 流程

完成标准：

- 页面仍能正常打开
- 至少有一张图完全通过新链路生成

### 任务 6：加入刷新任务状态

输出物：

- `datahub/jobs/refresh.py`
- 一张简单的任务状态表

完成标准：

- 至少能记录某个 dataset 的刷新开始、结束、结果

---

## 8. 第一阶段建议暂缓的事情

下面这些先别急：

- 全量前端重写
- 复杂拖拽布局
- 完整权限体系
- 多用户系统
- 实时 WebSocket 推送
- 通用插件系统

原因不是它们不重要，而是它们都建立在“数据对象和服务边界已经清楚”的前提上。

---

## 9. 建议的最小测试策略

下一阶段不要再只有脚本测试了，建议先补三类最小测试。

### 9.1 registry 测试

目标：

- 能正确加载 dataset/view/workspace 配置
- 缺字段时能给出清晰错误

建议位置：

- `tests/registry/`

### 9.2 transform 测试

目标：

- `rebase`
- `pct_change`
- `rolling_mean`
- `spread`

这些变换对固定输入能给出正确输出。

建议位置：

- `tests/transforms/`

### 9.3 service 测试

目标：

- `dataset_service`
- `view_service`

能从 registry + observations 组出正确数据。

建议位置：

- `tests/services/`

---

## 10. 一份更具体的本周执行顺序

如果现在就开始做，我建议按下面顺序推进。

### 第 1 步

新增目录和空文件骨架：

- `app/services/`
- `datahub/registry/`
- `datahub/storage/`
- `datahub/transforms/`
- `datahub/jobs/`
- `config/datasets/`
- `config/views/`
- `config/workspaces/`

### 第 2 步

在 `datahub/registry/models.py` 里定义：

- `DatasetDefinition`
- `ViewDefinition`
- `WorkspaceDefinition`

### 第 3 步

在 `datahub/registry/loader.py` 里实现：

- 读取单个 YAML
- 读取目录下全部 YAML
- 校验基本字段

### 第 4 步

在 `datahub/storage/registry_db.py` 里实现：

- 初始化三张 registry 表
- 写入 registry
- 查询 registry

### 第 5 步

挑 2 到 3 个最简单指标做迁移样板：

- `SOFR`
- `Fed Balance Sheet`
- `DXY`

先不要拿最复杂的商品期限结构做样板。

### 第 6 步

实现第一版 `view_service`：

- 读取 view
- 找到 dataset
- 拉 observations
- 应用 transform
- 返回 DataFrame

### 第 7 步

让当前 Streamlit 页面中的一个局部区域先试跑新链路。

建议方式：

- 新旧链路并存
- 通过一个小范围页面或一个测试 workspace 验证

---

## 11. 对你当前项目最务实的建议

如果只说一句最务实的话，那就是：

**先不要忙着把 UI 做得更花，先把 `dataset registry` 和 `view service` 立起来。**

因为一旦这两层站稳：

- 新增图表会更容易
- 新增数据源会更容易
- 搜索、对比、工作区这些终端能力才有稳定基础

反过来说，如果这两层不立起来，就算前端继续加功能，后面也会越来越难维护。

---

## 12. 建议的下一步实际动作

紧接着本文档之后，最适合进入代码实现的动作有两个。

### 方案 A

先搭最小骨架：

- 创建 `app/`、`datahub/`、`config/datasets/`、`config/views/`、`config/workspaces/`
- 补 `registry models + loader + registry_db`

这是最稳妥的下一步。

### 方案 B

直接做一条端到端样板链路：

- 选 `SOFR`
- 把它从旧 `indicator` 拆成 `dataset + view`
- 接入 `view_service`
- 在 Streamlit 里显示出来

这是最能快速验证方向的一步。

如果只选一个，我建议先做 **方案 A**，然后立刻接 **方案 B**。
