# 架构改进 Design Spec

**Date:** 2026-04-04
**Scope:** A1 配置统一 + A2 pipeline 化 + A4 降级状态 + A5 async 边界清理

---

## 决策记录

| 决策点 | 结论 |
|--------|------|
| 配置模型 | 新建 `CodeWikiConfig`（pydantic），替代 Config/AppConfig/Configuration/ConfigManager 全部四套 |
| 配置持久化 | keyring + JSON 废弃，TOML 唯一来源，凭证走 `env:` 语法 |
| CLI config 命令 | 重写为读写 TOML 文件，删除 keyring 路径 |
| 旧用户迁移 | hard fail + 提示迁移指令，不做自动迁移 |
| DocumentationGenerator | pipeline 化，8 个 stage，每个 stage 声明失败策略 |
| MetadataStage 顺序 | 最后（在 guide + postprocess 之后），收集完整 usage |
| 降级状态 | `GenerationResult(complete/degraded/failed)` + `ModuleSummary` 模块级结构化条目 |
| async 边界 | 每个路由的整段同步流水线搬进 `asyncio.to_thread()`，不逐函数包 |
| Web worker 重构 (A3) | 不在本轮范围 |
| Retry 收敛 (A6) | 已在 P5 完成，不在本轮范围 |

---

## A1: 配置模型统一为 `CodeWikiConfig`

### 现状

四套模型表达同一组概念：

| 文件 | 模型 | 用途 |
|------|------|------|
| `codewiki/src/config.py` | `Config` dataclass | 后端运行时 |
| `codewiki/src/config_loader.py` | `AppConfig` dataclass | TOML 解析 |
| `codewiki/cli/models/config.py` | `Configuration` / `AgentInstructions` | CLI 层 |
| `codewiki/cli/config_manager.py` | `ConfigManager` | keyring + JSON 持久化 |

### 目标态

一个 `CodeWikiConfig`（pydantic BaseModel），从 TOML 加载，CLI flags 覆写，后端直接消费。

### 加载链路

保留薄的 `config_loader` 入口，负责：
1. `tomllib.load()` → raw dict
2. `env:` secret 解析（遍历值，遇到 `env:VAR` 前缀则 `os.getenv(VAR)`）
3. provider/model 交叉校验（`resolve_model_ref()` 验证每个 model 能找到对应 provider）
4. `CodeWikiConfig.model_validate(resolved_dict)`

不是"文件直接变模型"——resolution 层保留，只是不再有 `AppConfig → Config` 的二次转换。

### 删除清单

- `codewiki/src/config.py` 中的 `Config` dataclass → 删除（路径常量保留在同文件）
- `codewiki/cli/models/config.py` 中的 `Configuration` / `AgentInstructions` → 删除
- `codewiki/cli/config_manager.py` → 整文件删除
- `codewiki/src/config.py` 中的 `set_cli_context()` / `is_cli_context()` / `_CLI_CONTEXT` → 删除

### CLI 配置命令重写

这是 **CLI 产品行为变更**，不只是内部重构：

| 命令 | 当前行为 | 新行为 |
|------|---------|--------|
| `config set <key> <value>` | 写入 `~/.codewiki/config.json` + keyring | 写入 `config.toml`（如果不存在则从 `config.example.toml` 创建） |
| `config get <key>` | 从 JSON + keyring 读取 | 从 `config.toml` 读取 |
| `config set api_key <value>` | 存入 keyring | 废弃。提示用户在 TOML 里写 `api_key = "env:MY_API_KEY"` 并设置环境变量 |
| `config agent` | 编辑 `AgentInstructions` 写入 JSON | 编辑 TOML 的 `[agent]` section |
| `config validate` | 验证 JSON + keyring | 验证 TOML（加载 + `validate_llm_credentials`） |

### 上下文构造

`CodeWikiConfig.context: Literal["cli", "web"]` 替代全局开关：

| 入口 | 构造方式 |
|------|---------|
| CLI adapter (`doc_generator.py`) | `CodeWikiConfig(context="cli", ...)` |
| Web worker (`background_worker.py`) | `CodeWikiConfig(context="web", ...)` |
| Standalone viewer (`visualise_docs.py`) | `CodeWikiConfig(context="web", ...)` |

同一个 task 一次性迁移，不存在两套机制并存的过渡期。

### Breaking Change 声明

**BREAKING CHANGE: legacy `~/.codewiki/config.json` + keyring 全部删除，不提供迁移命令。**

- 不做自动迁移，不提供 `config migrate` 命令
- 如果旧 JSON 存在且无 `config.toml`：**hard fail** + 提示
  ```
  ERROR: Legacy config (~/.codewiki/config.json) is no longer supported.
  Create a config.toml from config.example.toml and fill in your settings.
  API keys must use env: syntax (e.g. api_key = "env:MY_API_KEY").
  ```
- 不从 keyring 读取任何历史凭证
- `config validate` 的 legacy 分支一起删除
- 旧用户升级路径：手动新建 `config.toml`，API key 手工改成 `env:VAR`

### 影响文件

`codewiki/src/config.py`（删 Config dataclass，保留常量），`codewiki/src/config_loader.py`（输出 CodeWikiConfig），`codewiki/cli/models/config.py`（删除），`codewiki/cli/config_manager.py`（删除），`codewiki/cli/commands/config.py`（重写），`codewiki/cli/commands/generate.py`，`codewiki/cli/adapters/doc_generator.py`，`codewiki/src/fe/background_worker.py`，`codewiki/src/fe/visualise_docs.py`，以及所有 `from codewiki.src.config import Config` 的消费方

---

## A2: DocumentationGenerator pipeline 化

### 方案

- 新建 `codewiki/src/be/pipeline.py`：`PipelineStage` 协议、`PipelineContext` dataclass、`PipelineRunner`
- `PipelineContext` 携带每个阶段的输入/输出：config、components、module_tree、working_dir、gen_state、state_mgr、usage_stats、`GenerationResult`
- 每个 stage 声明 `failure_policy: Literal["fail_fast", "degraded_ok"]`

### 8 个阶段（顺序固定）

| # | Stage | 职责 | 失败策略 | 理由 |
|---|-------|------|---------|------|
| 1 | `GraphBuildStage` | 依赖分析 | `fail_fast` | 没有依赖图无法继续 |
| 2 | `IndexBuildStage` | 符号索引 | `degraded_ok` | 索引失败可继续，文档质量降低 |
| 3 | `ClusteringStage` | 模块聚类 | `fail_fast` | 没有模块树无法生成 |
| 4 | `StateInitStage` | ledger 初始化 + filename freeze | `fail_fast` | 没有状态账本无法断点续跑 |
| 5 | `ModuleGenerationStage` | 模块文档生成 | 特殊（见下） | 部分失败是常态 |
| 6 | `GuideStage` | guide 文档生成 | `degraded_ok` | 不影响模块文档 |
| 7 | `PostprocessStage` | docs_fixer | `degraded_ok` | 失败保留已有文档 |
| 8 | `MetadataStage` | metadata.json | `degraded_ok` | 模块文档已生成，metadata 失败不阻断文档产出。但 CLI/static 消费端会缺少 metadata，标记为 degraded |

### `ModuleGenerationStage` 特殊处理

不是简单的 pass/fail。Scheduler 返回 `ModuleSummary`，pipeline runner 据此判断：
- 全部 completed → stage 成功
- 部分 failed/skipped → stage 成功但 result 标记 `degraded`，详情写入 `ModuleSummary`
- 全部 failed → stage 失败，pipeline 中止

### `DocumentationGenerator.run()` 变成

```python
async def run(self):
    stages = [
        GraphBuildStage(), IndexBuildStage(), ClusteringStage(),
        StateInitStage(), ModuleGenerationStage(), GuideStage(),
        PostprocessStage(), MetadataStage(),
    ]
    runner = PipelineRunner(stages)
    result = await runner.execute(self._build_initial_context())
    return result  # GenerationResult
```

### 影响文件

`codewiki/src/be/pipeline.py`（新），`codewiki/src/be/documentation_generator.py`（瘦身为 pipeline 编排），`codewiki/cli/adapters/doc_generator.py`（消费 GenerationResult）

---

## A4: 显式降级状态

### 数据模型

```python
@dataclass
class ModuleFailure:
    doc_id: str
    error: str
    retried: bool  # 是否经过重试后仍然失败

@dataclass
class ModuleSkip:
    doc_id: str
    reason: str  # 如 "dependency module:xxx failed"

@dataclass
class ModuleSummary:
    completed: list[str]            # doc_ids
    failed: list[ModuleFailure]     # 结构化失败条目
    skipped: list[ModuleSkip]       # 因依赖失败跳过的 parent
    retried_then_succeeded: list[str]  # 重试后成功的 doc_ids
    total: int

@dataclass
class GenerationResult:
    status: Literal["complete", "degraded", "failed"]
    warnings: list[str]            # stage-level 降级原因
    module_summary: ModuleSummary   # 模块级结构化汇总
    metadata: dict                  # 完整 metadata（含 token_usage）
```

### 状态判定规则

| 条件 | status | 示例 |
|------|--------|------|
| 所有 stage 成功 + 所有模块 completed | `complete` | 正常运行 |
| 有 `degraded_ok` stage 失败（IndexBuild/Guide/Postprocess/Metadata） | `degraded` | 索引构建失败但文档已生成 |
| 部分模块 failed/skipped | `degraded` | 30 个模块完成 28 个，2 个失败 |
| `fail_fast` stage 失败 | `failed` | 依赖分析失败 |
| 所有模块 failed | `failed` | API key 无效导致全部失败 |

### 落盘

`metadata.json` 增加：
```json
{
  "generation_status": "degraded",
  "degradation_reasons": ["IndexBuildStage failed: timeout", "2 modules failed"],
  "module_summary": {
    "total": 30,
    "completed": ["module:cli", "module:auth_layer", "..."],
    "failed": [{"doc_id": "module:auth", "error": "rate limit", "retried": true}],
    "skipped": [{"doc_id": "module:auth_overview", "reason": "dependency module:auth failed"}],
    "retried_then_succeeded": ["module:cli"]
  }
}
```

### CLI 展示

| status | 展示 |
|--------|------|
| `complete` | 绿色 "Generation complete" |
| `degraded` | 黄色 "Generation completed with issues:" + 逐条列出 warnings + failed/skipped 模块 |
| `failed` | 红色 "Generation failed:" + 原因 |

### 影响文件

`codewiki/src/be/pipeline.py`（`GenerationResult`、`ModuleSummary` 定义），`codewiki/src/be/documentation_scheduler.py`（返回 `ModuleSummary`），`codewiki/cli/adapters/doc_generator.py`（消费 + 展示），`codewiki/src/be/documentation_generator.py`（metadata 落盘）

---

## A5: async 边界清理

### 方案

把每个路由里的**整段同步流水线**打包成一个同步函数，然后 `await asyncio.to_thread(sync_func)`。不逐函数零敲碎打。

### `routes.py` 改造

当前每个文档查看路由在 async 协程内直接做：JSON 加载 → fuzzy lookup → 目录扫描 → 文件读取 → markdown 渲染 → 模板渲染。全部阻塞 event loop。

改为：
```python
async def view_doc(self, request, filename):
    # 只做参数校验和路径安全检查（快，不阻塞）
    safe_path = self._validate_path(filename)

    # 整段同步流水线搬进线程
    def _render():
        content = load_and_render_doc(safe_path, self.docs_dir, self.module_tree)
        return content

    html = await asyncio.to_thread(_render)
    return HTMLResponse(html)
```

### `visualise_docs.py` 改造

同理：文件列表扫描 + markdown_to_html + heading 注入 + 模板渲染 整体包进 `to_thread()`。

### 不改的

- 路由签名不变（仍然是 async def）
- 架构不变（不引入 background task 或 worker pool）
- web worker 的 job 处理流程不动（A3 范围）

### 影响文件

`codewiki/src/fe/routes.py`，`codewiki/src/fe/visualise_docs.py`

---

## 实施顺序

```
A4 (GenerationResult + ModuleSummary 数据模型，独立)
  ↓
A2 (pipeline 化，依赖 A4 的数据模型)
  ↓
A1 (配置统一，影响面最大，放中间让 pipeline 先稳定)
  ↓
A5 (async 边界，独立，最后做风险最低)
```

A4 先做因为 A2 的 pipeline runner 需要 `GenerationResult` 和 `ModuleSummary` 类型。A1 放在 A2 之后是因为 pipeline 化改了 `run()` 签名和 `DocumentationGenerator` 接口，A1 再改 config 传递时可以基于新接口。A5 独立于其他三项。

---

## 不在范围内

- A3: Web worker 重构（共享 dict + 单线程队列）
- A6: Retry 收敛（已在 P5 完成）
- O4 Phase B: 全仓 f-string → structlog 结构化日志迁移
- GuideGenerator 拆分（可以在 pipeline 化后单独做）
