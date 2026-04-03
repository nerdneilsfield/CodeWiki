# 终态审阅修复 Design Spec

**Date:** 2026-04-04
**Scope:** 4 BLOCK + 8 HIGH + 6 MEDIUM/SUGGESTION = 18 项手术式修复

---

## 决策记录

| 决策点 | 结论 |
|--------|------|
| Web 层 H1-H3 | 这轮修正确性 bug（加锁、透传、持久化），不重构架构 |
| job_status 并发 | `threading.Lock` + 拷贝读（读时 `dict()` 快照） |
| H1/H2/H3 绑定 | 必须同批落地，不拆开 |
| H4 覆盖率 | 留在这轮，但作为独立 task |
| H5 模板缓存 key | 按模板字符串内容缓存（不是 `id(template)`） |
| H8 commit_id regex | 大小写不敏感：先 `lower()` 再 `^[a-f0-9]{4,40}$` |
| M9 datetime UTC | 整条 web/job/cache 链路一口气 UTC 化，不半改 |
| M5 CacheManager flush | dirty flag + 定时 flush + 进程退出时 flush |

---

## BLOCK 修复

### B1: 删除 sys.stdout import-time 替换

- 删除 `str_replace_editor.py:36` 的 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")`
- Python 3.12 不需要。这行破坏 pytest capture、Click 输出、web worker stdout

**影响文件:** `codewiki/src/be/agent_tools/str_replace_editor.py`

### B2: 裸 `except:` → `except Exception:`

- `cloning.py:99,155`：改为 `except Exception:`
- `generate.py:534`：改为 `except Exception:`
- 保留 KeyboardInterrupt/SystemExit 传播

**影响文件:** `codewiki/src/be/dependency_analyzer/analysis/cloning.py`, `codewiki/cli/commands/generate.py`

### B3: `assert` → 显式 `if ... raise`

- `str_replace_editor.py:201,391-404`：合约检查改为 `if not X: raise ValueError(...)`
- `scheduler.py:331`：`assert last_exc is not None` → `if last_exc is None: raise RuntimeError(...)`
- 确保 `-O` 模式下合约检查不被移除

**影响文件:** `codewiki/src/be/agent_tools/str_replace_editor.py`, `codewiki/src/be/documentation_scheduler.py`

### B4: CLI token 统计字段名对齐

- `doc_generator.py:182` 读 `total_input` / `total_output`
- `llm_usage.py:71` 落盘为 `total_input_tokens` / `total_output_tokens`
- 修 `doc_generator.py` 的读取 key：`token_usage.get("total_input_tokens", 0)` / `token_usage.get("total_output_tokens", 0)`

**影响文件:** `codewiki/cli/adapters/doc_generator.py`

---

## HIGH 修复

### H1+H2+H3: Web job_status 正确性修复（绑定批次）

三条必须同批落地——只做其中一两条会停在半结构化、半持久化的尴尬状态。

**H1: 加锁 + 拷贝读（通过 worker API 封装）**
- `BackgroundWorker` 加 `self._job_lock = threading.Lock()`
- 所有写操作（`_process_job` 里改 status/progress/error、`save_job_statuses`）持锁
- 新增公开方法封装读操作，路由层不直接碰 `_job_lock` 或 `job_status`：
  - `snapshot_jobs() -> dict[str, JobStatus]`：持锁拿 `dict(self.job_status)` 快照后返回
  - `snapshot_job(job_id) -> Optional[JobStatus]`：持锁拿单个 job 的拷贝
- 路由层统一改为调用 `self.background_worker.snapshot_jobs()` / `snapshot_job(job_id)`，在返回的快照上遍历/排序

**H2: 透传 GenerationResult 到 JobStatus**
- `models.py` 的 `JobStatus` 加字段：
  - `generation_status: Optional[str]`（`"complete"` / `"degraded"` / `"failed"`）
  - `degradation_reasons: list[str]`
  - `module_summary: Optional[dict]`（`ModuleSummary.to_dict()` 的输出）
- `background_worker.py` 完成后从 `GenerationResult` 提取写入 job

**H3: 失败 job 持久化**
- `_process_job` 的 `finally` 块里加 `self.save_job_statuses()`（不只是 except 分支）
- 无论成功、失败、异常都确保 job 状态落盘

**影响文件:** `codewiki/src/fe/background_worker.py`, `codewiki/src/fe/models.py`, `codewiki/src/fe/routes.py`

### H4: 启用覆盖率测量

- `pyproject.toml` 删 `-p no:cov`
- 加 `[tool.coverage.run]` 配置（`source = ["codewiki"]`, `omit = ["*/tests/*"]`）
- Makefile test target 加 `--cov=codewiki`
- 作为独立 task，不和运行时代码修复混在一起
- **注意：** coverage 启用不作为本轮运行时代码完成的验收门槛。它改的是工具链，不是业务修复。

**影响文件:** `pyproject.toml`, `Makefile`

### H5: `render_template()` 缓存 Jinja2 Environment

- 按**模板字符串内容**缓存编译后的 template（不是 `id(template)`）
- 用 `functools.lru_cache` 包一个 `_compile_template(template_str) -> Template` 函数
- `render_template()` 调 `_compile_template()` 取缓存结果，再 `.render(**context)`
- 已知模板是 module-level 常量，不会变化，缓存命中率 100%

**影响文件:** `codewiki/src/fe/template_utils.py`

### H6: `asyncio.get_event_loop()` → `asyncio.get_running_loop()`

- `scheduler.py:325,383` 两处
- Python 3.10+ 已废弃

**影响文件:** `codewiki/src/be/documentation_scheduler.py`

### H7: `ast_parser.py` 删除强制 `logger.setLevel(DEBUG)`

- 删除 `ast_parser.py:16` 的 `logger.setLevel(logging.DEBUG)`
- 让 structlog 全局配置控制级别

**影响文件:** `codewiki/src/be/dependency_analyzer/ast_parser.py`

### H8: `clone_repository()` 加 `commit_id` 校验（大小写不敏感）

- 函数入口：先 `commit_id = commit_id.lower()` 再 `re.match(r'^[a-f0-9]{4,40}$')`
- 接受大小写十六进制 SHA，统一转小写
- 同步更新 `routes.py` 的 `_COMMIT_RE` 为 `re.compile(r'^[a-fA-F0-9]{4,40}$')` 或在匹配前 `.lower()`

**影响文件:** `codewiki/src/fe/github_processor.py`, `codewiki/src/fe/routes.py`

---

## MEDIUM + SUGGESTION 修复

### M5: CacheManager 延迟写 last_accessed

- 加 `self._dirty = False` flag
- `get_cached_docs()` 命中时更新内存中的 `last_accessed` + 设 dirty，不立即写盘
- flush 策略（不引入额外 timer thread）：
  - 新增 `flush()` 方法：仅当 dirty 时写盘并重置 flag
  - 在 `add_to_cache()`、`remove_from_cache()`、`cleanup_expired_cache()` 这些本来就会写盘的方法里顺手调 `flush()`
  - 进程正常退出：`atexit.register(self.flush)`
  - 异常退出可接受丢失 last_accessed（这只影响 LRU 淘汰精度，不影响数据正确性）

**影响文件:** `codewiki/src/fe/cache_manager.py`

### M7: `_build_evidence_snippets()` 用 EdgeIndex

- 当前全量扫描 `index_products.edges`
- 改为按 symbol 查 `edge_index.callees_of(sid)`，和 P3 对 `_classify_edges` 的修法一致

**影响文件:** `codewiki/src/be/generation/context_pack.py`

### M8: CLI fallback metadata 用原子写

- `doc_generator.py:253` 的裸 `open("w")` → `file_manager.save_json()`
- 走原子写 + UTF-8

**影响文件:** `codewiki/cli/adapters/doc_generator.py`

### M9: 整条 web/job/cache 链路 UTC 化

不半改。一口气把以下全部改成 `datetime.now(timezone.utc)`：

- `routes.py`：job 创建时间、缓存时间
- `background_worker.py`：`created_at`、`completed_at`、超时检查
- `cache_manager.py`：`last_accessed`、`created_at`
- `models.py`：`JobStatus.created_at` / `completed_at` 默认值
- 落盘/读盘路径：`fromisoformat()` 解析已有数据时，如果无时区信息，按 UTC 处理（兼容旧数据）

**影响文件:** `codewiki/src/fe/routes.py`, `codewiki/src/fe/background_worker.py`, `codewiki/src/fe/cache_manager.py`, `codewiki/src/fe/models.py`

### S1: 删根目录残留 test 文件

- 删除 `test_format.py`、`test_math.py`、`test_math2.py`

### S5: `JOB_CLEANUP_HOURS = 24000` → `24`

**影响文件:** `codewiki/src/fe/config.py`

---

## 实施顺序

```
批次 1（BLOCK，无依赖，可并行）:
  B1 + B2 + B3 + B4

批次 2（H1+H2+H3 绑定批次）:
  H1 加锁 → H2 透传 → H3 持久化（同一 commit）

批次 3（HIGH 独立项，可并行）:
  H5 + H6 + H7 + H8

批次 4（MEDIUM + SUGGESTION）:
  M9 (UTC 化，影响面大放前) → M5 → M7 → M8 → S1 + S5

批次 5（独立）:
  H4 (覆盖率，工具链)
```

H4 最后做因为它改测试基础设施，不影响运行时代码。

---

## 不在范围内

- M1/M2: Pipeline stages 是 wrapper + PipelineContext Any 类型 —— 已知设计取舍
- M3/M4: CodeWikiConfig 部分字段 Any + 默认值分散 —— 后续迭代
- M6: ModuleTreeManager 写放大 —— 后续迭代
- M10: Web worker 单线程轮询架构 —— A3 范围
- M11: visualise_docs.py global 可变状态 —— 后续迭代
- S2-S4, S6-S8: generator_version、typing 迁移、ruff 规则、f-string logger、URLs 占位、emoji 日志 —— 后续清理
