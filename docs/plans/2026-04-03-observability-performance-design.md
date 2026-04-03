# 可观测性 + 性能修复 Design Spec

**Date:** 2026-04-03
**Scope:** O1-O5 可观测性 + P1-P6 性能，基于 5-agent 审计 + Codex 审核

---

## 决策记录

| 决策点 | 结论 |
|--------|------|
| Response token 计数 | 优先读 provider 真实 usage，fallback 本地估算并标记 `source=estimated` |
| 日志框架 | structlog（包装标准 logging） |
| CLILogger 处置 | 由 structlog console renderer 替代，CLILogger 废弃 |
| State 写盘策略 | dirty flag，coordinator done_queue 路径每次 flush；初始化/discovered/finally 也强制 flush |
| glossary/link_map 过滤 | A（依赖）> B（路径邻近）> C（token 上限截断） |
| call_llm retry 归属 | call_llm 改单次调用，各调用场景各自负责 retry |
| token 用量数据归宿 | LLMUsageStats 作为显式依赖传入各子系统，落盘到 metadata.json |

---

## O1+O3: LLM Token 用量追踪（合并）

**数据模型:**

```python
@dataclass
class LLMUsageStats:
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    # by_model: {"gpt-4o": {"input": N, "output": M, "requests": R}}

    def record(self, model: str, input_tokens: int, output_tokens: int,
               source: str = "api") -> None:
        """累加一次 LLM 调用的 token 用量。"""
        ...
```

**数据口径:**
- **agent 路径** (`agent_orchestrator.py`, `generate_sub_module_documentations.py`): 读 `result.usage()` 获取 `input_tokens`/`output_tokens`/`requests`
- **call_llm 非 agent 路径** (`docs_fixer`, `cluster_modules`, `naming`, `guide_generator`): 从 `response.usage.completion_tokens` 读真实值；如果 provider 不返回 usage（如 streaming），fallback 用 `count_tokens()` 并标记 `source=estimated`
- **不双算**: agent 内部的 call_llm 已包含在 `result.usage()` 中，non-agent 路径独立计数

**生命周期:**
- `LLMUsageStats` 实例在 `DocumentationGenerator.__init__()` 创建，挂为 `self.usage_stats`
- 作为显式参数传入 `AgentOrchestrator`、`GuideGenerator`、`docs_fixer.fix_docs()`、clustering 路径
- **落盘时机有两条路径:**
  - `DocumentationGenerator.run()` 路径: `run()` 结束时自动写入 `metadata.json`
  - CLI adapter 路径 (`doc_generator.py`): adapter 手动调 `generate_module_documentation()` + `GuideGenerator.run()` 后，从 `generator.usage_stats` 读取并写入 `metadata.json`（通过 `create_documentation_metadata()` 接收 stats 参数）
- 两条路径共用同一个 `LLMUsageStats` 实例，不会漏统计

**call_llm 改动:**
- `call_llm()` 返回值从 `str` 改为 `LLMCallResult(content: str, usage: LLMCallUsage | None)`
- `LLMCallUsage = {"input_tokens": int, "output_tokens": int, "source": "api" | "estimated"}`
- 所有调用方解包 `.content`，有需要的同时读 `.usage` 累加到 stats

**落盘格式 (metadata.json):**
```json
{
  "statistics": {
    "token_usage": {
      "total_input_tokens": 150000,
      "total_output_tokens": 80000,
      "total_requests": 45,
      "by_model": {
        "gpt-4o": {"input": 100000, "output": 60000, "requests": 30},
        "glm-4p5": {"input": 50000, "output": 20000, "requests": 15}
      }
    }
  }
}
```

**影响文件:** `codewiki/src/be/llm_services.py`, `codewiki/src/be/agent_orchestrator.py`, `codewiki/src/be/agent_tools/generate_sub_module_documentations.py`, `codewiki/src/be/documentation_generator.py`, `codewiki/src/be/guide_generator.py`, `codewiki/src/be/docs_fixer.py`, `codewiki/src/be/cluster_modules.py`, `codewiki/cli/adapters/doc_generator.py`

---

## O2: 运行时配置日志

- 在 `generate` 命令启动时，通过 structlog 以 INFO 级别无条件输出生效配置
- 输出内容: `main_model`, `cluster_model`, `fallback_model`, `max_tokens`, `max_concurrent`, `output_language`, `provider_count`
- 不依赖 `--verbose`
- **Scope:** 本轮只覆盖 CLI generate 路径。Web 启动路径的配置日志留给架构子项目（web 先不管）

**影响文件:** `codewiki/cli/commands/generate.py`

---

## O4: 日志统一到 structlog（分阶段）

**Phase A — 引入 structlog + 替换高价值入口:**
- 添加 `structlog` 依赖
- 新建 `codewiki/src/logging_setup.py`，提供两个配置函数：
  - `configure_cli_logging(verbose: bool)`: console renderer（带颜色），CLI 入口调用
  - `configure_web_logging()`: JSON renderer，web 入口调用
- **接线点（谁在什么时候调用）:**
  - CLI: `codewiki/cli/adapters/doc_generator.py` 的 `_configure_backend_logging()` 改为调用 `configure_cli_logging(verbose)`
  - Web: `codewiki/src/fe/web_app.py` 的 `create_app()` 中调用 `configure_web_logging()`（本轮只接线，web 功能本身不改）
  - Background worker: `codewiki/src/fe/background_worker.py` 的 worker 初始化时调用 `configure_web_logging()`
- 替换所有裸 `print()` 为 structlog logger（`cache_manager.py`, `github_processor.py`, `cloning.py`）
- `CLILogger` 废弃，由 structlog console renderer 替代

**Phase B — 逐步结构化（后续迭代）:**
- 把 `logger.info(f"message {var}")` 迁成 `logger.info("message", var=var)` 结构化格式
- 这不在本轮 plan 范围内——只标记方向，不承诺全仓改完

**影响文件:** `codewiki/src/logging_setup.py`(新), `pyproject.toml`, `codewiki/cli/adapters/doc_generator.py`, `codewiki/src/fe/web_app.py`, `codewiki/src/fe/background_worker.py`, `codewiki/src/fe/cache_manager.py`, `codewiki/src/fe/github_processor.py`, `codewiki/src/be/dependency_analyzer/analysis/cloning.py`, `codewiki/cli/utils/logging.py`

---

## O5: 非 verbose 模式下 INFO 可见

- structlog filter processor: codewiki namespace 的 INFO 始终输出
- 第三方库（`httpx`, `openai`, `pydantic_ai`, `httpcore`）保持 WARNING 压制
- 通过 structlog 的 `filter_by_level` processor 或 stdlib handler level 实现

**影响文件:** `codewiki/src/logging_setup.py`, `codewiki/cli/adapters/doc_generator.py`

---

## P1: GenerationStateManager 批量写盘

- 所有 mutation 方法（`mark_running`, `mark_completed`, `mark_failed`, `_add_task` 等）只修改内存 + 设 `_dirty = True`
- 新增 `flush()` 显式 API: 仅当 dirty 时写盘并重置 flag
- **flush 触发点:**
  - scheduler: coordinator 每收到一个 `done_queue` 消息后调 `flush()`
  - 初始化: `bulk_add_tasks()` 完成后调 `flush()`
  - discovered task: `register_discovered_task()` 完成后调 `flush()`
  - run finally: 强制 `flush()`
- 去掉当前每次 mutation 后的自动 `_save()`

**影响文件:** `codewiki/src/be/generation_state.py`, `codewiki/src/be/documentation_scheduler.py`, `codewiki/src/be/documentation_generator.py`

---

## P2: glossary/link_map 按模块过滤

**前置步骤 — 结构化数据模型改造:**

当前 `build_glossary()` 返回 `dict[str, str]`（term → definition 纯文本），`file_path` 只是拼进 definition string 里。需要改为结构化 entry:

```python
@dataclass
class GlossaryEntry:
    term: str
    definition: str
    symbol_id: str      # 来源 symbol 的 ID
    file_path: str      # 来源文件路径
    kind: str           # "class", "function", etc.
```

`build_glossary()` 返回 `dict[str, GlossaryEntry]`。

`link_map` 当前已经是 `dict[str, str]`（key_path → filename），结构足够。只需从 ledger 查 `doc_id` 对应的 `depends_on` 来确定相关模块。

**过滤流程:**
1. 从当前模块的 component list 提取 `symbol_id` set
2. **A (依赖):** 通过 `EdgeIndex` 查 `depends_on`/`depended_by` 的 symbol，扩展 set
3. **B (路径邻近):** 按 component 的 `file_path` 提取目录前缀，把同目录的 glossary entry 补入
4. 用 expanded symbol set 过滤 glossary；用模块依赖关系过滤 link_map
5. **C (token 截断):** 结果做 token 估算，超过上限（4000 tokens）按优先级截断（A 最高 → B → 路径越远越先截）。截断单位为整条 entry，不做 partial

**`format_context_pack_section()` 改造:**
- 接收过滤后的 glossary 和 link_map，不再接收全局 dict
- `build_context_pack()` 增加 `module_components` 参数用于过滤

**影响文件:** `codewiki/src/be/generation/glossary.py`, `codewiki/src/be/generation/context_pack.py`, `codewiki/src/be/agent_orchestrator.py`

---

## P3: 用 EdgeIndex 替代全量扫描

- `_classify_edges()` 在 `context_pack.py` 中当前每个模块遍历全部 edge list
- `EdgeIndex` 已存在（`index_products.edge_index`），支持按 symbol_id 查询
- 改为: 遍历模块的 `symbol_id` set，对每个 symbol 调用 `edge_index.callees_of(sid)` / `edge_index.callers_of(sid)` / `edge_index.edges_of(sid)`（真实 API），分类为 boundary/internal

**影响文件:** `codewiki/src/be/generation/context_pack.py`

---

## P5: call_llm retry 按调用场景收敛

**call_llm 改动:**
- 去掉内置 retry 循环（当前的 `for attempt, delay in enumerate([0] + _RETRY_DELAYS):`）
- 只保留: 创建 client → 调用 API → 返回 `LLMCallResult`
- `_RETRY_DELAYS`, `_parse_retry_after`, `_sleep_with_jitter` 移到 `documentation_scheduler.py`

**各场景 retry 归属:**

| 调用场景 | Retry 策略 | 理由 |
|----------|-----------|------|
| scheduler (module docs) | scheduler coordinator 管理，已有 retry_delays [10, 30, 90] | 主路径，需要可靠重试 |
| guide_generator | 已有 `_call_llm_with_fallback` + model fallback，保留 | 有自己的降级链 |
| docs_fixer (math/mermaid repair) | 单次调用，失败跳过 | best-effort 修复，不值得 retry |
| cluster_modules / naming | 单次调用，失败用 heuristic fallback | 已有 fallback 路径 |

**影响文件:** `codewiki/src/be/llm_services.py`, `codewiki/src/be/documentation_scheduler.py`, `codewiki/src/be/guide_generator.py`

---

## P6: parent input_hash 包含 child content hash

**计划阶段 (build_generation_tasks):**
- parent/overview task 的 `input_hash` 基线计算时，加入所有 child 的 `doc_id`（此时 child 还没完成，没有 content_hash）
- 这个基线 hash 用于判断"parent 的依赖结构是否变了"

**child 完成后的重算:**
- scheduler coordinator 在所有 child 完成、parent unblock 时，重算 parent 的 `input_hash`，此时包含 child 的 `content_hash`
- 和 ledger 中 parent 的 `input_hash` 比较：如果变了，parent 标记 stale 并重新生成
- 这保证: 子文档内容更新 → parent overview 自动重生成

**影响文件:** `codewiki/src/be/documentation_tree_utils.py`, `codewiki/src/be/documentation_scheduler.py`, `codewiki/src/be/documentation_overview.py`

---

## 实施顺序

```
Phase 1 (基础设施):
  O4-Phase-A (structlog 引入) → O5 (INFO 可见) → O2 (配置日志)

Phase 2 (token 追踪):
  O1+O3 (LLMUsageStats + call_llm 返回值改造)

Phase 3 (性能):
  P3 (EdgeIndex) → P6 (parent hash) → P2 (glossary 过滤) → P1 (批量写盘) → P5 (retry 收敛)
```

P5 放最后因为它改 `call_llm` 的返回类型和调用语义，影响面最大。O1+O3 也改 `call_llm` 返回值，所以放在 P5 之前——两次改 `call_llm` 合并成一次。

---

## 不在范围内

- O4 Phase B（全仓 f-string → 结构化日志迁移）—— 方向标记，不承诺本轮完成
- Web worker 重构 (A3)
- 配置模型统一 (A1)
- GuideGenerator 拆分
- DocumentationGenerator 进一步分解
