# LLM Middleware 统一调用层设计

## 背景

当前 LLM 调用存在两套路径，行为不一致：

- **`call_llm` 路径**（overview, guide, cluster, postprocess）：有模型路由，无 overflow 重试，无输出预留扣除
- **pydantic-ai Agent 路径**（主文档生成, 子模块生成）：有模型路由+overflow 重试，但逻辑散在 agent_orchestrator 里

导致的问题：`documentation_overview.py` 直接调 `call_llm`，200K prompt 发给 202K 上限的模型，overflow 后无重试直接崩溃。

## 目标

统一为三层架构，一种调用方式：

```
调用方
├── 单轮调用 (overview, guide, cluster, postprocess)
└── pydantic-ai Agent → 通过 MiddlewareModel 适配器
          ↓
    LLM 中间层 (LLMMiddleware)
    - 模型路由 (normal → long context)
    - Overflow 重试 (先切模型 → 再裁剪)
    - 输出预留扣除
    - 多轮对话历史裁剪
    - Usage 统计
          ↓
    底层 SDK (OpenAI / Anthropic)
```

## 设计

### 文件结构

| 文件 | 职责 |
|------|------|
| `llm_middleware.py`（新建） | `LLMMiddleware` 类 + `MiddlewareModel` 适配器 |
| `llm_services.py`（精简） | 底层 SDK 封装：client 创建/缓存、raw 调用、provider 解析、pydantic-ai Model 构建 |

### `llm_services.py` 保留内容

```python
# 底层 SDK
_create_client_for_model(config, model)     # SDK client 创建
_get_cached_openai_client(base_url, key)    # client 缓存 (lru_cache)
_get_cached_anthropic_client(key, url)      # client 缓存 (lru_cache)
_call_claude(client, model, prompt, ...)    # 原始 Anthropic 调用
_call_llm_streaming(client, model, ...)     # 原始 OpenAI 流式调用
resolve_model_ref(model, providers)         # provider 解析
_get_provider_config(config, model)         # provider 配置

# 新增：给中间层用的底层单次调用（从 call_llm 剥离，无路由无重试）
def raw_llm_call(prompt, config, model, temperature=0.0, stream=False) -> LLMCallResult

# 保留：给 MiddlewareModel 用的 pydantic-ai Model 构建
create_main_model(config)
create_fallback_models(config)
create_long_context_model(config)
```

### `llm_services.py` 删除内容

```python
call_llm()                  # → LLMMiddleware.call()
select_agent_model()        # → LLMMiddleware._route_model()
```

### `LLMMiddleware` 类

```python
import threading
from codewiki.src.be.llm_services import raw_llm_call, create_fallback_models, create_long_context_model
from codewiki.src.be.utils import count_tokens, _get_encoder
from codewiki.src.be.errors import LLMError, ErrorCategory

class LLMMiddleware:
    def __init__(self, config: CodeWikiConfig, usage_stats: LLMUsageStats | None = None):
        self._config = config
        self._usage_stats = usage_stats
        self._usage_lock = threading.Lock()

    # ── 单轮调用入口 ──────────────────────────────────────────

    def call(
        self,
        prompt: str,
        *,
        model: str | None = None,       # None = 自动路由
        temperature: float = 0.0,
        stream: bool = False,            # with_retry 在 timeout 后会注入 stream=True
        max_retries: int = 3,
        trim_step: int = 100_000,
    ) -> LLMCallResult:
        # 1. token 计数
        prompt_tokens = count_tokens(prompt)

        # 2. 模型路由（先路由再截断，避免 long-context 被 normal budget 误截）
        effective_model = self._route_model(model, prompt_tokens)

        # 3. 按路由后模型的上限截断（扣除输出预留）
        input_budget = self._input_budget_for_model(effective_model)
        if prompt_tokens > input_budget:
            prompt = self._truncate(prompt, input_budget)
            prompt_tokens = input_budget

        # 3. 调用 + overflow 重试
        current_prompt = prompt
        for attempt in range(max_retries + 1):
            try:
                result = raw_llm_call(current_prompt, self._config, effective_model, temperature, stream=stream)
                self._record_usage(result)
                return result
            except Exception as e:
                if not self._is_context_overflow(e):
                    raise
                # 第一次 overflow：切到 long context model
                lc_model = self._config.long_context_model
                if attempt == 0 and lc_model and effective_model != lc_model:
                    effective_model = lc_model
                    logger.warning("🔀 Overflow → 切换到 long context model: %s", lc_model)
                    continue
                # 后续：裁剪 prompt
                if attempt >= max_retries:
                    raise
                current_tokens = count_tokens(current_prompt)
                new_budget = max(current_tokens - trim_step, 10_000)
                current_prompt = self._truncate(current_prompt, new_budget)
                logger.warning(
                    "✂️ Overflow → 裁剪 prompt %dK → %dK (attempt %d/%d)",
                    current_tokens // 1000, new_budget // 1000,
                    attempt + 1, max_retries,
                )

    # ── pydantic-ai 适配器工厂 ────────────────────────────────

    def create_agent_model(self) -> "MiddlewareModel":
        return MiddlewareModel(self)

    # ── 内部方法 ──────────────────────────────────────────────

    def _route_model(self, explicit_model: str | None, tokens: int) -> str:
        """统一模型路由。explicit_model 非 None 时直接使用（如 cluster_model, repair_model）。"""
        if explicit_model:
            return explicit_model
        if (self._config.long_context_model
                and tokens > self._config.long_context_threshold):
            return self._config.long_context_model
        return self._config.main_model

    def _input_budget_for_model(self, model: str) -> int:
        """返回指定模型的输入 token 上限（已扣除输出预留）。"""
        if model == self._config.long_context_model:
            return self._config.long_context_max_input_tokens - self._config.max_tokens
        return self._config.max_input_tokens - self._config.max_tokens

    # 关键词集合，_is_context_overflow 使用，也可导出给 errors.py 共用
    _OVERFLOW_KEYWORDS = (
        "context_length", "too long", "maximum context",
        "input length", "range of input", "token limit",
        "max_tokens", "input_tokens_limit",
    )

    def _is_context_overflow(self, exc: Exception) -> bool:
        """统一 overflow 检测。覆盖 LLMError、pydantic-ai 异常、原始 SDK 异常。"""
        # 1. 已分类的 LLMError
        if isinstance(exc, LLMError) and exc.category == ErrorCategory.RESOURCE_EXHAUSTED:
            return True
        # 2. pydantic-ai 自身的异常
        try:
            from pydantic_ai.exceptions import UsageLimitExceeded, ModelHTTPError
            if isinstance(exc, UsageLimitExceeded):
                return True
            if isinstance(exc, ModelHTTPError) and exc.status_code == 400:
                msg = str(exc).lower()
                if any(k in msg for k in self._OVERFLOW_KEYWORDS):
                    return True
        except ImportError:
            pass
        # 3. 原始 openai SDK 异常
        try:
            import openai
            if isinstance(exc, openai.APIStatusError) and exc.status_code == 400:
                msg = str(exc).lower()
                if any(k in msg for k in self._OVERFLOW_KEYWORDS):
                    return True
        except ImportError:
            pass
        # 4. 兜底字符串匹配
        msg = str(exc).lower()
        return any(k in msg for k in self._OVERFLOW_KEYWORDS)

    def _truncate(self, text: str, max_tokens: int) -> str:
        """tiktoken 硬截断。"""
        enc = _get_encoder("gpt-4")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return enc.decode(tokens[:max_tokens])

    def _record_usage(self, result: LLMCallResult):
        if self._usage_stats and result.usage:
            with self._usage_lock:
                self._usage_stats.record(
                    result.model,
                    result.usage.input_tokens,
                    result.usage.output_tokens,
                )
```

### `MiddlewareModel` 适配器

```python
from pydantic_ai.models import Model, ModelResponse, ModelSettings, ModelRequestParameters

class MiddlewareModel(Model):
    """pydantic-ai Model 子类。每轮 request 经过中间层路由和保护。

    继承 Model ABC 以通过 pydantic-ai 的 infer_model() 类型检查。
    Agent(model=middleware.create_agent_model()) 需要 isinstance(model, Model) == True。
    """

    def __init__(self, middleware: LLMMiddleware):
        super().__init__()  # Model.__init__(settings=None, profile=None)
        self._middleware = middleware
        self._max_retries = 3

    async def request(self, messages, model_settings, model_request_parameters) -> ModelResponse:
        tokens = self._estimate_message_tokens(messages)
        model_name = self._middleware._route_model(None, tokens)
        real_model = self._resolve_pydantic_model(model_name)
        current_messages = messages

        for attempt in range(self._max_retries + 1):
            try:
                return await real_model.request(
                    current_messages, model_settings, model_request_parameters
                )
            except Exception as e:
                if not self._middleware._is_context_overflow(e):
                    raise
                lc = self._middleware._config.long_context_model
                if attempt == 0 and lc and model_name != lc:
                    model_name = lc
                    real_model = self._resolve_pydantic_model(model_name)
                    logger.warning("🔀 Agent overflow → long context model")
                    continue
                if attempt >= self._max_retries:
                    raise
                # 动态裁剪对话历史（按当前模型的实际上限）
                model_budget = self._middleware._input_budget_for_model(model_name)
                current_messages = self._trim_conversation(current_messages, model_budget)
                logger.warning("✂️ Agent overflow → 裁剪对话历史 (attempt %d/%d)",
                               attempt + 1, self._max_retries)

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters):
        """在 stream 建立阶段提供 overflow 保护：先切模型 → 再裁剪对话历史。

        pydantic-ai Agent 在传入 event_stream_handler 时走 request_stream()，
        这是主文档生成的实际路径，必须具备保护。
        注意：@asynccontextmanager 只能 yield 一次，因此 retry 只覆盖 stream 获取
        （__aenter__ 前）阶段；如果 overflow 发生在流消费阶段，则向上抛出。
        """
        tokens = self._estimate_message_tokens(messages)
        model_name = self._middleware._route_model(None, tokens)
        real_model = self._resolve_pydantic_model(model_name)
        current_messages = messages

        for attempt in range(self._max_retries + 1):
            try:
                async with real_model.request_stream(
                    current_messages, model_settings, model_request_parameters
                ) as stream:
                    yield stream
                    return  # 成功，退出重试循环
            except Exception as e:
                if not self._middleware._is_context_overflow(e):
                    raise
                lc = self._middleware._config.long_context_model
                if attempt == 0 and lc and model_name != lc:
                    model_name = lc
                    real_model = self._resolve_pydantic_model(model_name)
                    logger.warning("🔀 Agent stream overflow → long context model")
                    continue
                if attempt >= self._max_retries:
                    raise
                model_budget = self._middleware._input_budget_for_model(model_name)
                current_messages = self._trim_conversation(current_messages, model_budget)
                logger.warning("✂️ Agent stream overflow → 裁剪对话历史 (attempt %d/%d)",
                               attempt + 1, self._max_retries)

    def _trim_conversation(self, messages, budget_tokens: int):
        """保留首轮（system+user prompt），从尾部往前按 token 预算动态保留。"""
        head = messages[:2]
        tail = messages[2:]
        kept_tail = []
        used = self._estimate_message_tokens(head)
        for msg in reversed(tail):
            msg_tokens = self._estimate_message_tokens([msg])
            if used + msg_tokens > budget_tokens:
                break
            kept_tail.insert(0, msg)
            used += msg_tokens
        trimmed = len(tail) - len(kept_tail)
        if trimmed:
            logger.info("裁剪 %d 轮早期对话历史，保留 %d 轮", trimmed, len(kept_tail))
        return head + kept_tail

    def _estimate_message_tokens(self, messages) -> int:
        """从 pydantic-ai message 结构估算 token。"""
        total_chars = 0
        for msg in messages:
            for part in getattr(msg, "parts", []):
                text = getattr(part, "content", None) or ""
                if isinstance(text, str):
                    total_chars += len(text)
                args = getattr(part, "args", None)
                if args:
                    if isinstance(args, str):
                        total_chars += len(args)
                    elif isinstance(args, dict):
                        total_chars += sum(len(str(v)) for v in args.values())
        return total_chars // 3

    def _resolve_pydantic_model(self, model_name: str):
        """将模型名转成 pydantic-ai Model 对象。"""
        config = self._middleware._config
        if model_name == config.long_context_model:
            return create_long_context_model(config)
        return create_fallback_models(config)

    @property
    def model_name(self) -> str:
        """pydantic-ai 用于日志和 profile 查找。"""
        return self._middleware._config.main_model

    @property
    def system(self) -> str:
        """OpenTelemetry gen_ai.system 属性。"""
        return "openai"  # 底层 SDK 统一走 OpenAI 兼容接口

    def __getattr__(self, name: str):
        """未知属性转发到 primary model，满足 pydantic-ai 接口（如 supported_builtin_tools）。"""
        return getattr(self._resolve_pydantic_model(self._middleware._config.main_model), name)
```

### 调用方迁移

中间层只接管 **overflow 保护 + 模型路由**。调用方保留自己的 transient retry、cancel token、timeout、semaphore 等弹性逻辑。

| 调用方 | 现在 | 迁移后 | 调用方保留的外层逻辑 |
|--------|------|--------|---------------------|
| `documentation_overview.py` | `call_llm(prompt, config)` | `middleware.call(prompt)` | 无（当前也没有） |
| `guide_generator.py` | `with_retry(call_llm, ...)` + cancel + semaphore + timeout→stream fallback + model chain | `with_retry(middleware.call, ...)` + cancel + semaphore + timeout→stream fallback + model chain | 全部保留，仅替换内层 `call_llm` → `middleware.call` |
| `cluster_modules.py` | `with_retry_sync(call_llm, ...)` | `with_retry_sync(middleware.call, ...)` | `with_retry_sync` 保留 |
| `clustering/naming.py` | `with_retry_sync(call_llm, ...)` | `with_retry_sync(middleware.call, ...)` | `with_retry_sync` 保留 |
| `mermaid_validator.py` | `with_retry_sync(call_llm, ...)` + batch | `with_retry_sync(middleware.call, ...)` + batch | `with_retry_sync` + batch 保留 |
| `math_validator.py` | `with_retry_sync(call_llm, ...)` + batch | `with_retry_sync(middleware.call, ...)` + batch | `with_retry_sync` + batch 保留 |
| `agent_orchestrator.py` | `Agent(model=select_agent_model(...))` + 手动 overflow 循环 | `Agent(model=middleware.create_agent_model())` | 删除手动 overflow 循环（由 MiddlewareModel 接管） |
| `generate_sub_module_documentations.py` | `Agent(model=select_agent_model(...))` + 应用级 retry [5s,15s] | `Agent(model=middleware.create_agent_model())` + 应用级 retry [5s,15s] | 应用级 retry 保留（处理非 overflow 的 transient 错误） |

### `middleware` 实例传递

```python
# pipeline.py 初始化
middleware = LLMMiddleware(config, usage_stats=usage_stats)

# 传给各 stage
doc_generator = DocumentationGenerator(config, middleware=middleware)
overview_generator = generate_parent_module_docs(..., middleware=middleware)
guide_generator = GuideGenerator(config, middleware=middleware)
```

### `agent_orchestrator.py` 简化

删除以下逻辑（全部移入中间层）：
- `_is_context_overflow()` 方法
- `create_agent()` 中的模型选择逻辑（`select_agent_model`）
- `process_module()` 中的 context overflow retry 循环
- `_CONTEXT_TRIM_STEP`, `_MAX_CONTEXT_RETRIES` 常量

简化后：
```python
class AgentOrchestrator:
    def __init__(self, config, middleware: LLMMiddleware, ...):
        self._middleware = middleware

    def create_agent(self, module_name, components, core_component_ids):
        return Agent(
            model=self._middleware.create_agent_model(),
            ...
        )

    async def process_module(self, ...):
        agent = self.create_agent(...)
        # 不需要 overflow 循环，MiddlewareModel 内部处理
        result = await agent.run(prompt, deps=deps, ...)
        ...
```

### 并发安全

- `call()` 无可变状态（prompt/model/retry 全是局部变量）→ 天然并行安全
- `_route_model()`, `_is_context_overflow()`, `_truncate()` 纯函数 → 安全
- `_record_usage()` 用 `threading.Lock` 保护 → 安全
- SDK client 缓存用 `lru_cache` → 线程安全
- `MiddlewareModel.request()` 每次调用的 messages/retry 是局部变量 → 安全

### 不在中间层范围内

业务级截断留在调用方：
- `format_user_prompt` 中的文件内容截断/签名降级/紧凑元数据
- `_budget_child_docs` 中的段落级渐进填充
- `read_code_components` 中的工具返回值截断

中间层只做通用硬截断（最后一道防线）。

### 回归测试计划

4 组关键测试，覆盖此次改动最容易回归的路径：

#### 1. guide 路径的 timeout→stream fallback

```python
def test_call_accepts_stream_kwarg():
    """with_retry 在 timeout 后注入 stream=True，middleware.call 必须接受。"""
    middleware = LLMMiddleware(config)
    # mock raw_llm_call，验证 stream=True 被透传
    result = middleware.call(prompt, stream=True)
    assert raw_llm_call was called with stream=True
```

#### 2. Agent request_stream() overflow 保护

```python
@pytest.mark.asyncio
async def test_request_stream_overflow_switches_model():
    """Agent.run(event_stream_handler=...) 走 request_stream()，
    overflow 时必须切模型 + 裁剪历史，不能 fail-fast。"""
    middleware = LLMMiddleware(config_with_long_context)
    model = middleware.create_agent_model()
    # 第一次 request_stream 抛 overflow → 切 long context model
    # 第二次成功
    # 验证 _resolve_pydantic_model 被调用时用了 long_context_model

@pytest.mark.asyncio
async def test_request_stream_overflow_trims_history():
    """切模型后仍然 overflow → 裁剪对话历史。"""
    # 构造 10 轮对话历史，总 token > budget
    # 验证裁剪后 messages 数量减少，首轮保留，最新轮保留
```

#### 3. long-context 路由后按正确上限截断

```python
def test_route_to_long_context_uses_long_budget():
    """路由到 long-context model 后，截断预算用 long_context_max_input_tokens，
    不是 max_input_tokens。"""
    config = make_config(max_input_tokens=200_000, long_context_max_input_tokens=800_000)
    middleware = LLMMiddleware(config)
    # 构造 300K token prompt
    # 验证：路由到 long context → 不被截断（300K < 800K - max_tokens）

def test_normal_model_uses_normal_budget():
    """路由到普通模型时，截断预算用 max_input_tokens。"""
    # 构造 300K token prompt，无 long_context_model
    # 验证：被截断到 max_input_tokens - max_tokens
```

#### 4. 三类异常均命中 overflow 判定

```python
@pytest.mark.parametrize("exc", [
    # pydantic-ai UsageLimitExceeded
    UsageLimitExceeded(usage=..., limits=...),
    # pydantic-ai ModelHTTPError with 400
    ModelHTTPError(status_code=400, message="Range of input length should be [1, 202745]"),
    # openai.APIStatusError with 400
    openai.BadRequestError("context_length_exceeded", response=mock_response(400), body={}),
    # LLMError RESOURCE_EXHAUSTED
    LLMError("too long", ErrorCategory.RESOURCE_EXHAUSTED, 400),
    # 各种错误消息格式
    LLMError("Range of input length should be [1, 202745]", ErrorCategory.NON_RETRYABLE_CLIENT, 400),
    LLMError("input_tokens_limit of 400000", ErrorCategory.NON_RETRYABLE_CLIENT, 400),
])
def test_is_context_overflow_detects_all_variants(exc):
    middleware = LLMMiddleware(config)
    assert middleware._is_context_overflow(exc) is True

@pytest.mark.parametrize("exc", [
    LLMError("model not found", ErrorCategory.NON_RETRYABLE_CONFIG, 404),
    openai.APIStatusError("rate limit", response=mock_response(429), body={}),
    ValueError("empty response"),
])
def test_is_context_overflow_rejects_non_overflow(exc):
    middleware = LLMMiddleware(config)
    assert middleware._is_context_overflow(exc) is False
```
