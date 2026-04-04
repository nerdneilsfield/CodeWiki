# CodeWiki

**把任意代码仓库转化为结构化、可导航的技术文档 —— 基于静态分析与 LLM 代理。**

CodeWiki 通过语言感知的解析器（AST + tree-sitter）扫描你的仓库，构建符号表和依赖图，将相关组件聚类为模块，然后调度 LLM 代理基于代码证据撰写文档。最终产出一组互相链接的 Markdown 文件，包含架构图、API 参考和跨模块引用。

[English Documentation](README.md) | [论文](https://arxiv.org/abs/2510.24428)

---

## 核心能力

1. **分析** —— 支持 9 种编程语言（Python、TypeScript、JavaScript、Java、C、C++、C#、Go、Rust）
2. **索引** —— 提取符号、导入、调用和继承关系，附带置信度与源码证据
3. **聚类** —— 使用图算法（目录先验 + SCC 收缩 + Louvain 社区发现）将组件聚类为逻辑模块，再由 LLM 约束性命名
4. **生成** —— 以证据驱动的 prompt 生成模块文档，每条行为断言都引用符号 ID 或源码位置
5. **校验** —— 链接检查、标题锚点一致性、Mermaid/Math lint 及可配置的严格门禁

## 快速开始

### 安装

```bash
pip install git+https://github.com/nerdneilsfield/CodeWiki.git
```

需要 Python 3.12+ 和 Node.js 14+（用于 Mermaid 校验）。

### 配置

创建一个 TOML 配置文件并填入 provider 凭证：

```bash
codewiki config init          # 在当前目录生成 config.toml
$EDITOR config.toml           # 填写 API key 环境变量和模型名
```

生成的文件（单 provider 最小示例）：

```toml
[generation]
main_model    = "openai/gpt-4o-mini"
cluster_model = "openai/gpt-4o-mini"

[[providers]]
name      = "openai"
type      = "openai_compatible"
base_url  = "https://api.openai.com/v1"
api_keys  = ["env:OPENAI_API_KEY"]
model_list = ["gpt-4o-mini"]
```

导出对应环境变量后校验配置：

```bash
export OPENAI_API_KEY=sk-...
codewiki config validate --config config.toml
```

### 生成文档

```bash
# 为本地仓库生成文档
codewiki generate /path/to/your/repo --config config.toml

# 生成中文文档
codewiki generate /path/to/repo --config config.toml --language zh

# 生成带 GitHub Pages 查看器的文档
codewiki generate /path/to/repo --config config.toml --github-pages
```

<details>
<summary><strong>完整 CLI 选项</strong></summary>

```
codewiki generate [REPO_PATH] [选项]

输出:
  --output DIR              输出目录（默认: ./docs）
  --create-branch           为生成的文档创建 git 分支
  --github-pages            生成 index.html 查看器
  --static                  预渲染为独立 HTML 页面
  --no-cache                强制全量重新生成

语言:
  --language CODE           输出语言: en, zh, zh-tw, ja, ko, fr, de, es

模型:
  --main-model NAME         主 LLM 模型
  --cluster-model NAME      模块命名模型
  --long-context-model NAME 超长 prompt 模型
  --long-context-threshold N 长上下文切换阈值

限制:
  --max-tokens N            最大响应 token 数（默认: 32768）
  --max-depth N             层级分解深度（默认: 2）
  --max-concurrent N        并行模块 worker 数（默认: 3）
  --max-retries N           补填重试次数（默认: 2）

过滤:
  --include PATTERNS        逗号分隔的文件包含模式
  --exclude PATTERNS        逗号分隔的文件排除模式
  --focus MODULES           重点文档化的模块

定制:
  --doc-type TYPE           api, architecture, user-guide, developer
  --instructions TEXT       自定义 agent 指令
  --verbose                 显示详细进度
```

</details>

### 输出结构

```
docs/
├── overview.md              # 从这里开始 — 仓库架构和入口
├── module_name.md           # 各模块文档
├── module_tree.json         # 层级模块结构
├── metadata.json            # 生成元数据
├── _lint_report.json        # 后处理 lint 结果
└── index.html               # 交互式查看器（使用 --github-pages）
```

---

## 架构

CodeWiki 将仓库处理分为五个层次，每层基于前一层的产出：

```
源代码
    │
    ▼
┌─────────────────────┐
│ 1. 索引层            │  AST + tree-sitter → 符号表、导入图、
│                      │  组件卡片、IMPORTS/CALLS/EXTENDS 边
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 2. 图层              │  带置信度 + 证据引用的类型化边，
│                      │  EdgeIndex 查询（callers_of / callees_of）
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 3. 聚类层            │  目录先验 → SCC 收缩 → Louvain →
│                      │  LLM 命名 → 命名冻结 → 稳定性度量
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 4. 生成层            │  证据驱动 prompt + 符号卡片 +
│                      │  边界边 + 术语表 + 链接映射
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ 5. 后处理层          │  链接校验、标题锚点一致性、
│                      │  Mermaid/Math lint + 降级、LintReport
└─────────────────────┘
```

<details>
<summary><strong>各层详解</strong></summary>

### 索引层

使用语言专用适配器解析源文件：
- **PythonIndexAdapter** —— 使用 `ast` 模块提取类、方法、导入、签名（含 kwonlyargs）、可见性（`__all__` 检测）和导入路径解析
- **TSJSIndexAdapter** —— 使用 tree-sitter 提取 TypeScript/JavaScript 的类/函数/导入，支持相对导入解析（.ts/.tsx/.js/.jsx 探测）
- **GenericIndexAdapter** —— 兜底适配器，将现有依赖分析器的 `Node` 对象转换为 `Symbol` 格式

产出：`SymbolTable`（按 id/file/name/qualified_name 的 O(1) 查找）、`ImportGraph`（文件级导入追踪与路径解析）、`ComponentCard`（面向 LLM 的摘要，含截断 docstring 和 top-5 边）。

### 图层

从索引构建类型化关系边：
- **IMPORTS** —— 来自已解析的导入语句（HIGH 置信度）
- **CALLS** —— 来自函数/方法调用点（置信度取决于解析质量）
- **EXTENDS** —— 来自类继承（从签名解析）

每条边都携带 `evidence_refs`（文件 + 行范围）和 `confidence`（HIGH/MEDIUM/LOW）。未解析的引用以 `to_unresolved` 保留，而非丢弃。

`EdgeIndex` 提供 O(1) 查询：`callers_of(symbol)`、`callees_of(symbol)`、`edges_of(symbol, type_filter)`、`dependency_subgraph(symbol_set)`。

### 聚类层

用确定性图算法流水线取代 LLM 驱动的分组：
1. **目录先验** —— 按顶层包目录分组组件
2. **SCC 收缩** —— 将循环依赖合并为超级节点（Tarjan 算法）
3. **Louvain 社区发现** —— 注入目录先验边（权重 2.0），固定种子（42）保证确定性
4. **LLM 命名** —— 约束为仅生成标题和描述（不允许重新分组成员）；失败时降级为启发式命名
5. **命名冻结** —— 当模块成员未变化时，复用上一次的标题/路径以防止漂移
6. **稳定性度量** —— Jaccard 相似度、路径稳定性、模块 ID 跨运行一致性

输出经过 `ModuleTree` schema 校验，发现不变量违规（重复组件、缺失分配、路径不唯一）时立即失败。

### 生成层

向 LLM prompt 注入证据丰富的上下文：
- **Context pack** —— 组件级精确的符号卡片、边界/内部边、证据片段
- **术语表** —— 公共 API 术语及 docstring 定义，作为共享上下文注入
- **链接映射** —— 基于稳定路径的模块交叉引用（使用 `module_doc_filename()`）
- **证据规则** —— 系统 prompt 块，要求每条断言引用 `symbol_id` 或 `file:line`

三条 prompt 通路（system、leaf、overview）全部接收证据规则。递归子模块生成通过 `CodeWikiDeps` 继承 `global_assets`。

### 后处理层

校验并修复生成的文档：
- **链接校验** —— 扫描内部 `[text](file.md#anchor)` 链接，验证文件存在性和标题锚点
- **标题锚点** —— `heading_to_slug()` 作为唯一规则源，渲染器和校验器共用，重复标题自动去重（-1、-2 后缀）
- **Mermaid 降级** —— 无法修复的图表替换为 `text` 代码块 + 错误注释
- **Math 降级** —— 行内公式 → 反引号代码；展示公式 → `latex` 围栏代码块
- **LintReport** —— JSON 报告保存到 `_lint_report.json`
- **严格门禁** —— `Config.postprocess_strict = True` 时，无法修复的问题抛出 `LintError`

</details>

---

## 韧性机制

CodeWiki 内置了 LLM 操作的错误处理、重试逻辑和取消支持。

**结构化错误分类** —— LLM SDK 异常被分类为不同类别（瞬态错误、认证错误、客户端错误、配置错误、资源耗尽），系统据此决定重试、切换到下一个模型还是立即失败。

**自动退避重试** —— 瞬态错误（429、500、502、503、超时）触发带抖动的指数退避。API 返回的 `Retry-After` 头部会被尊重。认证错误只重试一次。不可重试的错误立即传播。

**Streaming 降级** —— 对配置中标记了 `stream = true` 的模型，超时错误会触发以 streaming 模式重试。这对有严格非流式超时的 provider 很有帮助。当前版本仅 `openai_compatible` 类型的 provider 支持此功能。

```toml
# 为特定模型启用 streaming 降级
model_list = [
  "gpt-4o-mini",
  {name = "gpt-4.1", stream = true},
]
```

**协作式取消** —— 长时间运行的生成任务可通过 Web API（`POST /api/jobs/{job_id}/cancel`）取消。取消检查点分布在：pipeline 阶段边界、scheduler 任务之间、重试等待期间，以及每次 guide 章节 LLM 调用之前。

---

## Docker 部署

<details>
<summary><strong>Docker 配置</strong></summary>

```bash
cd docker
cp env.example .env
# 编辑 .env 填写你的 API 凭证
docker compose up -d
```

Web 界面地址：`http://localhost:8000`。

**环境变量：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | （必填） | LLM 提供商 API 密钥 |
| `LLM_BASE_URL` | （必填） | API 基础 URL |
| `MAIN_MODEL` | `claude-sonnet-4` | 主模型 |
| `FALLBACK_MODEL_1` | `glm-4p5` | 降级模型 |
| `CLUSTER_MODEL` | （同 MAIN_MODEL） | 模块命名模型 |
| `APP_PORT` | `8000` | Web 界面端口 |

</details>

---

## Web 界面

通过浏览器提交 GitHub 仓库 URL，查看生成的文档：
- 深色/浅色模式（跟随系统偏好）
- 可折叠侧边栏，带模块树导航
- 每页自动生成目录
- 代码高亮（highlight.js）
- 数学公式渲染（KaTeX）
- 移动端适配
- 进行中的生成任务可取消

---

## 配置参考

<details>
<summary><strong>全部配置选项</strong></summary>

CodeWiki 通过一个 TOML 文件集中管理所有配置。运行 `codewiki config init` 获取带注释的模板文件，然后按需编辑。

```toml
[runtime]
output_dir         = "docs"       # Markdown 输出目录
max_depth          = 2            # 模块树深度
max_concurrent     = 3            # 并行 LLM worker 数
max_retries        = 2            # 遗漏模块的补填轮数
output_language    = "zh"         # "en" | "zh" | "ja" | …
postprocess_strict = false        # 是否在 lint 问题无法修复时阻断构建

[tokens]
max_tokens                = 32768
max_token_per_module      = 36369
max_token_per_leaf_module = 16000
long_context_threshold    = 200000

[generation]
# 所有模型字段使用 "provider名/模型名" 格式
main_model      = "openai/gpt-4o-mini"
cluster_model   = "openai/gpt-4o-mini"
fallback_models = ["openai/gpt-4o-mini"]
# long_context_model = "openai/gpt-4o"   # 可选

[agent]
# doc_type            = "architecture"   # api | architecture | user-guide | developer
# focus_modules       = ["src/core"]
# custom_instructions = ""

# ── Providers ────────────────────────────────────────────────────────────────
# api_keys 使用 env: 引用 —— 变量在生成时读取，不写入配置文件
[[providers]]
name       = "openai"
type       = "openai_compatible"
base_url   = "https://api.openai.com/v1"
api_keys   = ["env:OPENAI_API_KEY"]
model_list = [
  "gpt-4o-mini",                          # 纯字符串：stream 默认 false
  {name = "gpt-4o", stream = true},        # dict 形式：启用超时时的 streaming 降级
]

# 多 provider 可以共存；模型通过 provider 名引用
# [[providers]]
# name              = "claude"
# type              = "claude"
# api_keys          = ["env:ANTHROPIC_API_KEY"]
# anthropic_version = "2024-02-15"
# model_list        = ["claude-sonnet-4-5-20250929"]
```

**配置相关命令：**

```bash
codewiki config init                                       # 生成 config.toml 模板
codewiki config validate --config config.toml             # 结构校验
codewiki config validate --config config.toml --check-secrets  # 同时验证 env 变量是否已设置
codewiki config show     --config config.toml             # 显示已解析的配置
```

**传递配置到 generate：**

```bash
# 显式指定路径（推荐）
codewiki generate /path/to/repo --config config.toml

# 通过环境变量指定（适用于 Docker / CI）
export CODEWIKI_CONFIG=/path/to/config.toml
codewiki generate /path/to/repo
```

**旧版路径（已废弃）：**

`codewiki config set` 和 `codewiki config agent` 仍然可用，写入
`~/.codewiki/config.json + 系统钥匙链`，将在后续版本移除。
迁移方式：运行 `codewiki config init` 后将原有设置复制进 TOML 文件。

</details>

---

## 支持的语言

| 语言 | 适配器 | 提取内容 |
|------|--------|---------|
| Python | `ast` 模块 | 类、方法、导入、签名、可见性、`__all__` |
| TypeScript | tree-sitter | 类、方法、函数、导入、导出 |
| JavaScript | tree-sitter | 类、方法、函数、导入 |
| Java | tree-sitter | 类、方法、接口 |
| C | tree-sitter | 函数、结构体、头文件引入 |
| C++ | tree-sitter | 类、函数、命名空间、头文件引入 |
| C# | tree-sitter | 类、方法、接口 |
| Go | tree-sitter | 函数、结构体、接口 |
| Rust | tree-sitter | 函数、结构体、trait、impl |

Python 和 TypeScript/JavaScript 拥有增强适配器，支持导入解析和调用提取。其他语言使用通用适配器进行基础符号提取。

---

## 开发

```bash
# 克隆并以开发模式安装
git clone https://github.com/nerdneilsfield/CodeWiki.git
cd CodeWiki
uv sync --extra dev
pre-commit install

# 运行本地质量检查
uv run ruff check .
uv run ruff format --check .
uv run ty check

# 运行测试
python -m pytest tests/ -q

# 索引/聚类/生成/后处理专项测试
python -m pytest tests/test_index_*.py tests/test_clustering_*.py \
    tests/test_generation_*.py tests/test_postprocess_*.py -q

# 运行所有 pre-commit hooks
pre-commit run --all-files
```

---

## 性能

测试集：30 个仓库，语言覆盖 Python / JS / TS / C# / Java / C / C++，单仓最大代码量 1.4M 行：

| 类别 | CodeWiki | DeepWiki |
|------|----------|----------|
| 高级语言（Python, JS, TS） | **79.14%** | 68.67% |
| 托管语言（C#, Java） | **68.84%** | 64.80% |
| 系统语言（C, C++） | 53.24% | 56.39% |
| **总体** | **68.79%** | 64.06% |

---

## 引用

```bibtex
@article{codewiki2025,
  title={Evaluating AI's Ability to Generate Holistic Documentation for Large-Scale Codebases},
  author={...},
  journal={arXiv preprint arXiv:2510.24428},
  year={2025}
}
```

## 许可证

MIT
