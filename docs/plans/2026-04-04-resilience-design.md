# 韧性改进 Design Spec

**Date:** 2026-04-04
**Scope:** #5 结构化错误分类 + #6 可取消 Pipeline + #2 LLM retry wrapper + streaming fallback

---

## 决策记录

| 决策点 | 结论 |
|--------|------|
| 错误层次 | `LLMError` / `PipelineError` / `CancellationError` 三层独立，不继承 |
| 异常分类位置 | `errors.py` 提供 `classify_llm_exception(exc) -> LLMError`，`call_llm` 只在最外层调用，不把分类逻辑塞进 call_llm |
| retry wrapper | `with_retry` 是显式 wrapper，不嵌入 `call_llm`。调用方选择是否包 |
| scheduler retry | 保持自己的 coordinator retry，不包 `with_retry`。但错误分类改用 `LLMError.is_retryable` |
| guide_generator retry | 每个 model：TRANSIENT → `with_retry(max_retries=2)`；NON_RETRYABLE → 立即跳下一个 model |
| streaming fallback 范围 | 第一版仅 `openai_compatible` + model 标记 `stream=true` + timeout 类错误时切一次 |
| streaming 配置 | model 级别 `stream = true/false`，默认 false |
| 取消模型 | cooperative cancellation，`threading.Event`，边界点检查 |
| 取消终态 | `GenerationResult.status` 增加 `"cancelled"`，`JobStatus` 同步 |
| 实施顺序 | #5 → #6 → #2（streaming fallback 在 #2 内作为受限子集） |

---

## #5 结构化错误分类

### 新文件：`codewiki/src/be/errors.py`

**ErrorCategory 枚举：**
```
RETRYABLE_TRANSIENT   — 429, 500, 502, 503, 529, timeout, connection error
RETRYABLE_AUTH        — 401, 403（凭证刷新机会，只重试一次）
NON_RETRYABLE_CLIENT  — 400, 404（坏输入，重试无意义）
NON_RETRYABLE_CONFIG  — 缺 API key、model 不存在（配置问题）
RESOURCE_EXHAUSTED    — context_length_exceeded、quota exceeded
```

**LLMError(Exception)：**
- `category: ErrorCategory`
- `status_code: int | None`
- `is_retryable: bool` property（TRANSIENT 或 AUTH）
- 不继承 PipelineError

**PipelineError(Exception)：**
- `category: ErrorCategory`
- `stage: str`
- 不继承 LLMError。可以通过 `__cause__` 链接 LLMError

**CancellationError(Exception)：**
- 独立。不归入 retry。pipeline runner 和 retry wrapper 遇到直接 re-raise

**classify_llm_exception(exc) -> LLMError：**
- 输入：SDK 原始异常（`openai.APIStatusError`、`openai.APITimeoutError`、`anthropic.APIStatusError` 等）
- 输出：分类后的 `LLMError`
- 处理 `APITimeoutError`（无 status_code）→ TRANSIENT
- 处理 `context_length_exceeded` 在 400 响应体中 → RESOURCE_EXHAUSTED
- 已知 SDK/transport/timeout 异常 → 按 status code 分类
- 已知 config/input 异常（`ValueError`, `KeyError`）→ NON_RETRYABLE_CONFIG
- 其他未知异常 → **保持原样 re-raise**，不包装成 LLMError。尽早暴露未知 bug 比默默重试更安全

**call_llm 改动（极小）：**
```python
# codewiki/src/be/llm_services.py — 最外层
try:
    # ... 现有 provider 分发逻辑 ...
    return LLMCallResult(content=content, usage=usage, model=model)
except CancellationError:
    raise
except Exception as exc:
    raise classify_llm_exception(exc) from exc
```

不把分类逻辑塞进 `call_llm` 内部——`call_llm` 只负责在出口处调一下 `classify_llm_exception`。

### PipelineRunner 消费错误分类

```python
# pipeline.py — execute()
except CancellationError:
    ctx.result.status = "cancelled"
    break
except PipelineError as exc:
    if exc.is_retryable and stage.failure_policy == "degraded_ok":
        ctx.result.add_warning(str(exc))
    else:
        ctx.result.mark_failed(str(exc))
        break
except Exception as exc:
    # 未分类 → 按原有逻辑
    ...
```

### Scheduler 消费错误分类

`documentation_scheduler.py` 的 `_retry_delay` 改用 `LLMError.is_retryable` 替代当前的 `isinstance(exc, UnexpectedModelBehavior)` 检查：
- `LLMError.is_retryable` → 退避重试
- `LLMError(category=RESOURCE_EXHAUSTED)` → 跳过重试（和当前 `_is_context_length_error` 逻辑一致）
- `LLMError(category=NON_RETRYABLE_*)` → delay=0 立即重试一次（让 agent 用不同参数重试）

**影响文件：** `codewiki/src/be/errors.py`(新), `codewiki/src/be/llm_services.py`, `codewiki/src/be/pipeline.py`, `codewiki/src/be/documentation_scheduler.py`

---

## #6 可取消的 Pipeline

### 新文件：`codewiki/src/be/cancellation.py`

**CancellationToken：**
```python
class CancellationToken:
    def __init__(self):
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        """Raise CancellationError if cancelled."""
        if self._cancelled.is_set():
            raise CancellationError("Operation cancelled")
```

- `threading.Event` 底层——跨线程安全
- 不用 `run_in_executor` 做 sleep——async 代码中用 `asyncio.sleep` + 每次 sleep 前 `check()`
- 不中断正在执行的 HTTP 请求——cooperative，只在边界点停

### 检查粒度

| 位置 | 时机 |
|------|------|
| `PipelineRunner.execute()` | 每个 stage 前 |
| scheduler coordinator | 每个 module 从 `done_queue` 消费后 |
| `with_retry` | 每次 retry sleep 前 |
| `GuideGenerator` | 每次 guide section LLM 调用前（多轮串行生成是最长耗时点之一）|

`GuideGenerator` 构造函数接受 `cancel_token`，在 `_call_llm_with_fallback` 入口处 `cancel_token.check()`。

### 取消终态

`GenerationResult.status` 增加 `"cancelled"` 值。**所有 status 类型位点都要一起改：**

- `codewiki/src/be/pipeline.py`：`Literal["complete", "degraded", "failed"]` → `Literal["complete", "degraded", "failed", "cancelled"]`
- `codewiki/src/fe/models.py`：`JobStatus.status` 增加 `"cancelled"` 值；`JobStatus.generation_status` 同步
- `codewiki/src/fe/models.py`：`JobStatusResponse.status` 同步
- `codewiki/cli/adapters/doc_generator.py`：展示分支补 `"cancelled"` → 红色 "Generation cancelled by user"
- `codewiki/src/fe/routes.py`：web 状态展示补 `"cancelled"` 分支
- `codewiki/src/fe/background_worker.py`：`_process_job` 的 `CancellationError` 捕获设 `job.status = "cancelled"`

### BackgroundWorker 接入

```python
class BackgroundWorker:
    def __init__(self, ...):
        self._cancel_tokens: dict[str, CancellationToken] = {}

    def cancel_job(self, job_id: str) -> bool:
        token = self._cancel_tokens.get(job_id)
        if token:
            token.cancel()
            return True
        return False

    def _process_job(self, job_id: str):
        token = CancellationToken()
        self._cancel_tokens[job_id] = token
        try:
            # ... 传入 pipeline context ...
        finally:
            self._cancel_tokens.pop(job_id, None)
```

### Web API

`POST /api/jobs/{job_id}/cancel` → 调用 `worker.cancel_job(job_id)`

### PipelineContext

加字段：`cancel_token: CancellationToken | None = None`

**影响文件：** `codewiki/src/be/cancellation.py`(新), `codewiki/src/be/pipeline.py`, `codewiki/src/be/documentation_scheduler.py`, `codewiki/src/be/guide_generator.py`, `codewiki/src/fe/background_worker.py`, `codewiki/src/fe/routes.py`, `codewiki/src/fe/models.py`, `codewiki/cli/adapters/doc_generator.py`

---

## #2 LLM retry wrapper + streaming fallback

### 新文件：`codewiki/src/be/llm_retry.py`

**`with_retry` — 显式 async wrapper：**

```python
async def with_retry(
    operation: Callable[..., Awaitable[T]],
    *args,
    max_retries: int = 3,
    cancel_token: CancellationToken | None = None,
    on_timeout_use_stream: bool = False,
    **kwargs,
) -> T:
```

- 指数退避：0.5s base, 2x, cap 32s, + random jitter 25%
- Retry-After header 优先（通过 `LLMError` 的 response headers 提取）
- 错误分类驱动：
  - `LLMError(RETRYABLE_TRANSIENT)` → 退避重试
  - `LLMError(RETRYABLE_AUTH)` → 重试一次
  - `LLMError(NON_RETRYABLE_*)` → 立即 raise
  - `CancellationError` → 立即 raise
- 每次 sleep 前 `cancel_token.check()`
- timeout 后 streaming fallback：如果 `on_timeout_use_stream=True` 且错误是 timeout，下次 retry 传入 `stream=True`

**`LLMRetryExhausted(Exception)`：** 所有 retry 耗尽后的终态异常，携带 `last_error` 和 `attempts`。

### Streaming 恢复

**TOML 配置（model 级别，`model_list` item 多态）：**

```toml
[[providers]]
name = "openai"
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_keys = ["env:OPENAI_API_KEY"]
model_list = [
  "gpt-4o-mini",
  {name = "gpt-4o", stream = true},
]
```

`model_list` 的每个 item 可以是：
- `str` — 纯模型名，默认 `stream = false`
- `dict` — `{"name": "...", "stream": true, ...}` 带选项

**Loader 改动（`config_loader.py`）：**
- `_load_provider_configs` 解析 `model_list` 时判断 item 是 str 还是 dict
- 解析后存为标准化格式：`ResolvedModel` 增加 `stream: bool = False` 字段
- `resolve_model_ref()` 返回的 `ResolvedModel` 携带 `stream` 属性
- `call_llm` 可通过 `resolved.stream` 知道当前 model 是否支持 streaming

**call_llm 改动：**
- 增加 `stream: bool = False` 参数
- 恢复 `_call_llm_streaming`（之前 P5 删除的），但第一版仅对 `openai_compatible` 生效
- Claude 和其他 provider 第一版不支持 streaming fallback
- streaming 返回的 `LLMCallResult.usage` 标记 `source="estimated"`（streaming 不返回 usage）

**自动切换逻辑（在 `with_retry` 内）：**

```python
# with_retry 内部
if on_timeout_use_stream and isinstance(error, LLMError) and _is_timeout(error):
    kwargs["stream"] = True  # 下次 retry 用 streaming
```

仅在以下条件全部满足时切换：
1. `on_timeout_use_stream=True`（调用方显式启用）
2. 错误是 timeout 类（`APITimeoutError`、Cloudflare 524）
3. `resolve_model_ref()` 确认当前 model 配置了 `stream = true`
4. provider 是 `openai_compatible`

### 各调用场景接入

| 调用方 | 方式 |
|--------|------|
| scheduler | **不用 `with_retry`**。保持自己的 coordinator retry。`_retry_delay` 改用 `LLMError.is_retryable`。**不参与 streaming mode 决策**——streaming fallback 只在 direct call_llm callers（guide/fixer/clustering）内由 `with_retry` 管理 |
| guide_generator `_call_llm_with_fallback` | 每个 model 上用 `with_retry(max_retries=2, on_timeout_use_stream=model_has_stream)`。TRANSIENT retry 当前 model；NON_RETRYABLE 立即跳下一个 model |
| docs_fixer | `with_retry(max_retries=1)` — best-effort |
| cluster_modules / naming | `with_retry(max_retries=1)` — 失败走 heuristic |

**影响文件：** `codewiki/src/be/llm_retry.py`(新), `codewiki/src/be/llm_services.py`, `codewiki/src/config_loader.py`, `codewiki/src/codewiki_config.py`, `codewiki/src/be/guide_generator.py`, `codewiki/src/be/docs_fixer.py`, `codewiki/src/be/cluster_modules.py`, `codewiki/src/be/clustering/naming.py`, `codewiki/src/be/documentation_scheduler.py`

---

## 实施顺序

```
#5 错误分类（基础，其他两项依赖它）
  ↓
#6 取消（依赖 CancellationError 定义）
  ↓
#2 retry wrapper（依赖错误分类 + CancellationToken）
  ↓
streaming fallback（#2 内的受限子集，最后做）
```

---

## 不在范围内

- Pipeline Hook 系统（#1）—— 等有真实消费者再做
- Priority Job Queue（#3）—— 等 web worker 重构时一起做
- Tool 注册表（#4）—— YAGNI
- Claude provider streaming —— 第一版不做
- 强制中断正在执行的 HTTP 请求 —— 只做 cooperative cancellation
