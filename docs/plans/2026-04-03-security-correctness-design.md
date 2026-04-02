# 安全 + 正确性修复 Design Spec

**Date:** 2026-04-03
**Scope:** 9 项手术式修复（S1-S3 安全, C1-C6 正确性），不改系统形状，只堵洞

---

## 决策记录

| 决策点 | 结论 |
|--------|------|
| HTML sanitizer | nh3 |
| Mermaid 渲染 | 客户端渲染，sanitizer 不放 SVG 标签 |
| 配置源 | TOML 唯一来源，删除所有 `os.getenv()` fallback 和 `load_dotenv()` |
| Legacy env vars | 全删，Docker 改为挂载 TOML 文件 |
| LLM 返回空 content | 抛异常走 retry 路径，`call_llm()` 契约不变：成功返回非空 str，失败抛异常 |
| pending_count 竞态 | 直接重构为 coordinator coroutine 模型，不做补丁式修复 |

---

## S1: HTML 消毒 — 防止 Stored XSS

**问题:** `templates.py` 的 `{{ content | safe }}` 和 `visualise_docs.py` 的 `html.unescape()` → bare f-string 绕过 Jinja2 autoescaping。恶意仓库的代码注释/docstring 中的 `<script>` 标签可被 LLM 原样复制到生成的 HTML。

**方案:**

- 新建 `codewiki/src/fe/html_sanitizer.py`
- 用 `nh3.clean()` 做消毒，allowlist 只覆盖标准 markdown HTML 标签
- 不放 SVG、style、foreignObject（Mermaid 是客户端 JS 从文本渲染，不需要服务端 SVG）
- 两个注入点在传入 Jinja2 / f-string 前调用 `sanitize_html()`

**Allowlist:**

标签: `h1-h6, p, br, hr, ul, ol, li, dl, dt, dd, strong, em, b, i, u, s, del, ins, code, pre, blockquote, kbd, a, img, table, thead, tbody, tfoot, tr, th, td, caption, div, span, sup, sub, details, summary`

属性: `a.href/title/id/class, img.src/alt/title/width/height, div.class/id/data-nav/data-nav-sub, span.class/id, code.class, pre.class, td/th.align/colspan/rowspan`

不允许: `style` 属性、`on*` 事件属性、`script/iframe/object/embed/form/input` 标签

URL scheme 限制: 只允许 `http`、`https`、`mailto`。禁止 `javascript:`、`data:`、`vbscript:` 等可执行 scheme（通过 `nh3.clean()` 的 `url_schemes` 参数控制）

**影响文件:** `codewiki/src/fe/html_sanitizer.py`(新), `codewiki/src/fe/templates.py`, `codewiki/src/fe/visualise_docs.py`, `pyproject.toml`(加 nh3 依赖)

---

## S2: commit_id 服务端校验

**问题:** `github_processor.py` 的 `git checkout commit_id` 中 `commit_id` 来自 web form，仅客户端 HTML pattern 校验，可绕过。

**方案:**

- `routes.py` 的 `index_post()` 中，`commit_id.strip()` 后加 `re.match(r'^[a-f0-9]{4,40}$')` 校验
- 不匹配返回 HTTP 400
- 空字符串放行（表示不指定 commit）

**影响文件:** `codewiki/src/fe/routes.py`

---

## S3: 配置源统一为 TOML

**问题:** `config.py` 有 `os.getenv("LLM_API_KEY", "sk-1234")` 等硬编码 fallback，`load_dotenv()` 在 import 时执行污染环境。同时 `Config`/`AppConfig`/`Configuration` 三套模型表达同一概念。

**方案:**

- 删除 `config.py` 中所有 `os.getenv()` 调用和 module-level 常量赋值
- 删除 `load_dotenv()` 的 import-time 调用
- `Config` dataclass 的字段全部由 `config_loader.py` 从 TOML 填充
- 环境变量通过 TOML 的 `env:VAR_NAME` 语法引用（`config_loader.py` 已支持 `env:` 前缀解析）
- 启动校验：验证主模型能解析到有 API key 的 provider（在 `llm_services.py` 加 `validate_llm_credentials()`)，而不是检查 legacy 字段
- Docker 部署改为挂载 TOML 文件，更新 `docker/env.example` 说明
- CLI flags 可覆写 TOML 值（已有机制）

**影响文件:** `codewiki/src/config.py`, `codewiki/src/config_loader.py`, `codewiki/src/be/llm_services.py`, `codewiki/cli/commands/generate.py`, `docker/env.example`, `config.example.toml`

---

## C1: LLM 返回空/null content 防护

**问题:** `llm_services.py:359` 的 `response.choices[0].message.content` 可为 None（max_tokens 截断）或 `response.choices` 为空（content_filter）。None 会导致下游写入 `"None"` 字符串到文档。

**方案:**

- `choices` 为空时，raise `ValueError("LLM returned empty choices")`
- `content` 为 None 时，raise `ValueError("LLM returned null content (finish_reason=...)")`
- 这两种异常走正常的 retry 路径（和其他 LLM 错误一样），blast radius 和现有错误处理一致
- `call_llm()` 的返回类型契约不变：成功时返回非空 `str`，失败时抛异常

选择抛异常而不是返回空字符串的原因：返回空字符串会改变全局语义，所有调用方都需要加空值检查。抛异常只影响 `call_llm()` 内部，调用方无需改动。

**影响文件:** `codewiki/src/be/llm_services.py`（仅此一个文件）

---

## C2: save_text 编码 + 原子写

**问题:** `utils.py` 的 `save_text` 缺 `encoding="utf-8"`（WSL 下可能用 cp1252 损坏非 ASCII 内容），且直接写目标文件无原子性（进程中断导致截断文件）。

**方案:**

- 加 `encoding="utf-8"`
- 改用 `tempfile.mkstemp` + `os.fdopen` + `os.replace` 原子写模式（和 `save_json` 一致）
- `load_text` 同步加 `encoding="utf-8"`

**影响文件:** `codewiki/src/utils.py`

---

## C3: save_json 编码

**问题:** `utils.py:29` 的 `os.fdopen(tmp_fd, "w")` 缺 `encoding="utf-8"`，同 C2 原因。

**方案:**

- 加 `encoding="utf-8"` 到 `os.fdopen` 调用

**影响文件:** `codewiki/src/utils.py`

---

## C4: generation_state.json 加载容错

**问题:** `generation_state.py:182` 的 `json.load` 无 try/except。文件损坏（上次 crash 导致部分写入）直接 crash 整个生成流程，而不是回退到空 state 重新生成。

**方案:**

- `json.load` 包 `try/except (json.JSONDecodeError, OSError)`，损坏时 log warning 返回空 state
- 单条 task 解析失败（`TypeError`/`KeyError`）跳过该 task，log warning，不 crash 整个加载

**影响文件:** `codewiki/src/be/generation_state.py`

---

## C5: 调度协调模型重构

**问题:** `documentation_scheduler.py:265-273` 中 worker 分散操作 `pending_count` dict，`queue.put` 在锁外面，存在竞态。补丁式把 put 移进锁里只是治标。

**方案:**

- 引入 coordinator coroutine 作为依赖判定的单点：
  - `work_queue: asyncio.Queue` — coordinator 放入就绪任务，worker 取出执行
  - `done_queue: asyncio.Queue` — worker 完成后报告 `(doc_id, success: bool)`
  - coordinator 从 `done_queue` 消费，更新依赖计数，判断父任务是否就绪，就绪则放入 `work_queue`
- Worker 只做：从 `work_queue` 取任务 → 执行 → 往 `done_queue` 报告结果
- 只有 coordinator 操作 `pending_count`——单协程单点写入，无并发访问，无需锁
- coordinator 和 worker 通过 `asyncio.Queue` 通信，不共享 dict
- 保留现有的 retry/fallback/tqdm 逻辑，只改协调结构

**影响文件:** `codewiki/src/be/documentation_scheduler.py`

---

## C6: str_replace_editor 写入失败上报

**问题:** `str_replace_editor.py:792-800` 的 `write_file` 写入失败时静默 append 到 `self.logs` 并 return None。agent 以为写入成功，不会重试。直到后续 fill-pass 才发现文件缺失——但此时 retry 已用尽。

**方案:**

- 写入失败时 raise `PermissionError`，不再静默 return
- pydantic_ai 工具框架会将异常转为工具错误返回给 LLM，LLM 可据此调整行为

**影响文件:** `codewiki/src/be/agent_tools/str_replace_editor.py`

---

## 实施顺序

```
C2+C3 (同一文件, 最简单) → C1 → C4 → C6 → S2 → S3 (影响面最大) → C5 (重构) → S1 (需加依赖)
```

C2+C3 先上是因为修完编码后后续所有文件写入都是安全的。S3 放中间因为影响文件多需要仔细验证。C5 和 S1 最后因为改动最大。

---

## 不在范围内

- Web worker 重构（A3）—— 用户决定 web 先不管
- 配置模型统一（A1）—— 属于架构子项目
- 日志体系统一（O4）—— 属于可观测性子项目
- GuideGenerator 拆分 —— 属于架构子项目
