# LLM 生成 Code Wiki 文献调研报告

> 调研日期：2026-04-06
> 调研范围：2024-2026 年间关于使用大语言模型自动生成仓库级代码文档/Wiki 的研究论文

---

## 1. 研究背景与动机

开发者约 **58% 的工作时间**用于理解代码（Xia et al., 2017），高质量文档能显著降低这一成本。然而手动编写和维护文档极其耗时，且文档与代码的不一致（文档腐烂）是软件工程领域的长期痛点。

随着 LLM 在代码理解方面能力的飞速提升，研究界开始探索用 LLM **自动生成仓库级（repository-level）文档**——不再局限于函数/方法级注释，而是生成涵盖架构概览、模块交互、数据流图、使用指南的**完整知识库（Code Wiki）**。

---

## 2. 核心论文详细分析

### 2.1 RepoAgent（清华大学 / OpenBMB，2024.02）

**论文**：*RepoAgent: An LLM-Powered Open-Source Framework for Repository-level Code Documentation Generation*
**arXiv**：2402.16667 | **代码**：github.com/OpenBMB/RepoAgent

#### 方法
- **AST 解析** → 构建项目树（仓库/目录/文件/类/函数层级）
- **Jedi 库**提取双向引用关系（Caller/Callee），构建 DAG
- **拓扑排序**自底向上生成文档，子节点文档作为父节点 prompt 的一部分
- 固定 5 部分输出：功能描述、参数说明、代码详解、注意事项、输入/输出示例
- **Git pre-commit hook** 驱动增量文档更新

#### 核心创新
- **首个提出自动增量更新机制**的仓库级文档框架
- 利用确定性工具（Jedi）精确识别引用关系，召回率接近 100%
- 引用感知的 prompt 构建，显著提升文档上下文相关性

#### 评估结果
- 在 Transformers / LlamaIndex 仓库上，生成文档**胜率 70%-91%** 优于人工文档
- 参数识别准确率 GPT-4 达 96%-100%

#### 局限
- 仅支持 Python（依赖 Jedi）
- 文档粒度为 Class/Function 级，无架构级概览

---

### 2.2 HGEN（圣母大学，2024.08）

**论文**：*Supporting Software Maintenance with Dynamically Generated Document Hierarchies*
**arXiv**：2408.05829

#### 方法
- **Sentence-BERT 嵌入** + **5 种聚类算法共识投票**（OPTICS、Spectral、Agglomerative、Affinity Propagation、K-means）
- 聚类重要性评分：`importance = (α·log(s) + h) · v`（凝聚度 × 投票得分 × 规模）
- **六阶段流水线**：代码摘要 → 聚类 → 文档生成 → 去重细化 → 簇内追溯 → 簇间追溯
- 可递归迭代生成更高层级文档（需求 → Epic）
- **双重去重机制**（Stage 3 + Stage 5）

#### 核心创新
- **首个多层次文档生成框架**：源码 → 设计规格 → 需求 → Epic
- 多算法共识聚类 + 离群点清洗，概念覆盖率远超基线
- 簇间追溯链接降低孤儿 artifact 数量

#### 评估结果
- 概念覆盖率：**87.5%-100%**（基线仅 6.3%-50%）
- 追溯 mAP：94%-96.7%
- **9 个工业项目试点**，涵盖 C#/C++/TS/Java/C/Go，获得积极反馈

#### 局限
- 无依赖图分析（纯语义聚类）
- 管道复杂度高，可复现性挑战
- 未比较不同 LLM 的效果差异

---

### 2.3 DocAgent（Meta AI，2025.04，ACL 2025）

**论文**：*DocAgent: A Multi-Agent System for Automated Code Documentation Generation*
**arXiv**：2504.08725 | **代码**：github.com/facebookresearch/DocAgent

#### 方法
- **Navigator 模块**：AST 解析 → 依赖图 → Tarjan 算法折叠循环依赖 → **拓扑排序**确定处理顺序
- **五专职 Agent 协作**：
  - **Reader**：分析代码，输出结构化信息请求（XML）
  - **Searcher**：使用静态分析工具 + 外部知识 API 响应请求
  - **Writer**：按规范格式生成文档草稿
  - **Verifier**：评估质量，可触发 Writer 重写或 Reader 新一轮信息收集
  - **Orchestrator**：管理迭代流程 + 自适应 token 截断
- Reader-Searcher 多轮交互，Verifier 回环机制形成质量闭环

#### 核心创新
- **首个多 Agent 协作的代码文档生成系统**
- 增量式上下文构建：利用已生成的依赖文档，消除上下文爆炸
- **三维评估框架**：完整性（Completeness）+ 帮助性（Helpfulness）+ 真实性（Truthfulness）
- Truthfulness 通过依赖图验证引用实体真实性，量化幻觉程度

#### 评估结果
- 完整性：DA-CL **0.953**（基线 0.314-0.815）
- 帮助性：DA-GPT **3.88/5**（基线 1.51-2.95）
- 真实性：DA-GPT **95.74%**（Chat-GPT 仅 61.10%）
- 消融实验证实拓扑排序是核心贡献

#### 局限
- 仅支持 Python
- 仅静态分析，无动态运行时行为理解
- 文档粒度为函数/类级，无仓库级架构文档

---

### 2.4 RepoSummary（北京大学，2025.10）

**论文**：*RepoSummary: Feature-Oriented Summarization and Documentation Generation for Code Repositories*
**arXiv**：2510.11039

#### 方法
- **Eclipse JDT Core 静态分析** → 提取文件/方法实体 + import/invoke 关系 → 构建邻接矩阵
- **Sentence-BERT 语义相似度矩阵** + 邻接矩阵 = 混合权重
- **Leiden 社区发现算法**（CPM 目标函数）两阶段聚类：文件级 → 方法级
- 自动调优分辨率参数 γ：Stability + Separation - Small-Cluster Fraction
- **CoT 推理**生成功能特性（Feature）→ 聚合为 Epic
- 输出层次结构：Epic → Feature → File → Method（带双向追溯链）

#### 核心创新
- **首个按功能特性（而非目录结构）组织文档**的方法
- **Leiden 算法** + 语义相似度的混合聚类（区别于纯结构/纯语义）
- **方法级追溯链**（精度远超 HGEN 的文件级）

#### 评估结果
- 功能覆盖率：从基线 61.2% 提升至 **71.1%**
- 文件级追溯 Recall：从 29.9% 提升至 **53.0%**（+77%）
- 在 3 个 Java 基准仓库（Dronology/eTour/iTrust）上验证

#### 局限
- 仅支持 Java
- 评估数据集规模有限（3 仓库 + 26 commit）
- 聚类结果与真实功能边界可能不完全一致

---

### 2.5 CodeWiki（墨尔本大学 / FPT AI Center，2025.10）

**论文**：*CodeWiki: Automated Repository-Level Documentation at Scale* / *CodeWiki: Evaluating AI's Ability to Generate Holistic Documentation for Large-Scale Codebases*
**arXiv**：2510.24428v5

#### 方法
- **Tree-Sitter AST 解析** → 提取函数/类/模块及依赖关系 → 有向依赖图
- **拓扑排序**识别入口点 → 递归分区生成**模块树（Module Tree）**
- **递归文档生成**：每个叶子模块分配专属 Agent（具备源码访问、模块树上下文、依赖图遍历能力）
- **动态委派（Dynamic Delegation）**：当模块复杂度超阈值时，Agent 自主将子模块委派给子 Agent 递归处理（最大深度 3 层）
- **全局跨模块引用注册表**避免内容重复
- 自底向上合成父模块文档（含架构图、数据流图、序列图）

#### 核心创新
- **首个开源仓库级文档框架**，明确对标 DeepWiki（闭源）
- **7 语言支持**：Python/Java/JS/TS/C/C++/C#
- **动态委派机制**：运行时自适应决定是否拆分，解决大型代码库无法放入上下文的问题
- **CodeWikiBench**：首个仓库级文档评估基准，层次化 rubric + 多 Agent Judge

#### 评估结果
- CodeWiki-sonnet-4：**68.79%**（DeepWiki 64.06%，+4.73%）
- 高层语言（Python/JS/TS）表现最佳：79.14%（+10.47% vs DeepWiki）
- C/C++ 最弱：53.24%（反而低于 DeepWiki 的 56.39%）
- 14 个额外仓库验证：Trino（1.5M LOC Java）提升 +29.53%

#### 局限
- C/C++ 低层构造理解困难
- LLM 评估存在固有偏差

---

### 2.6 CodeMap（JHU / SMU / Fudan，2025.04 → ICPC 2026）

**论文**：*Understanding Codebase like a Professional! Human-AI Collaboration for Code Comprehension*
**arXiv**：2504.04553v3 | **会议**：ICPC 2026（里约热内卢，2026.04）

#### 方法
- 对 8 名专业代码审计师进行半结构化访谈，提炼出**三层认知框架**：全局（架构）→ 局部（模块/函数调用）→ 细节（逐行代码）
- **CodeMap 原型**：React + D3-Graphviz Web 应用，后端 GPT-4o + RAG
- 生成两类可视化：**业务组件图**（全局）+ **函数调用图**（局部）
- 用户点击节点即可层级切换，提问时自动携带当前上下文

#### 核心创新
- **首次从代码审计师（而非普通开发者）视角**设计代码理解工具
- **认知对齐框架**：系统设计完全与"全局→局部→细节"三层理解链对齐
- 交互驱动的按需生成，减少 LLM 文字阅读时间 **79%**

#### 评估结果
- 小型代码库 3 轮迭代可稳定收敛；大型（130 文件）用 GPT-4.1 达 **99% 准确率**
- 超大型（668 文件）仅约 27%，上下文限制仍是瓶颈
- 用户研究（15 人）：感知直觉性 9/9 vs ChatGPT 2/9，感知有用性 9/9 vs 7/9

#### 对本项目的启发
- 在 Wiki 中引入**全局业务组件地图**作为导航层
- 文档信息架构可参考审计师的三层认知框架重构

---

### 2.7 ArchView（SERC Lab，2026.03）

**论文**：*LLM-based Automated Architecture View Generation: Where Are We Now?*
**arXiv**：2603.21178

#### 方法
- **340 个开源仓库** × **13 种实验配置** = **4,137 个架构视图**，首个大规模实证评估
- 对比：零样本/少样本/Claude Code(GPA)/自研 **ArchView**（多 Agent 框架）
- ArchView = Prompt Builder（关注点规格驱动）+ View Generator（符号无关）+ Image Renderer（编译纠错循环）
- 输出 PlantUML 格式架构图

#### 核心发现
- **通用编码 Agent（Claude Code）在架构图任务上表现最差**（71.8% 失败率），甚至不如简单 few-shot
- ArchView 最佳：清晰度失败率 **22.6%**，人工评估细节层级成功率 **50%**
- **LLM 存在根本性粒度失配**：倾向在代码层级操作，难以提升到架构抽象层级
- 完整性是所有方法的共同瓶颈（失败率均 >73%）

#### 对本项目的启发
- 扩展到架构图生成需要**领域知识注入**（关注点规格、ISO 42010 视图类型）
- 简单摘要 → LLM → 图表的路径**效果不佳**，需要专门的架构抽象步骤

---

### 2.8 Google Code Wiki（Google Cloud，2025.11）

**产品**：codewiki.google | **发布**：2025 年 11 月 13 日（公开预览）

#### 技术方案
- **代码知识图谱**：Tree-sitter 解析 → 组件为节点、关系为边 → 图数据库
- **Agentic RAG**：向量搜索（语义检索）+ 图遍历（依赖导航），Agent 根据问题类型自动选择工具
- **PR 增量更新**：每次 PR 合并后自动扫描并重新生成受影响文档
- **Gemini 驱动聊天**：以最新 Wiki 为上下文回答仓库问题

#### 核心特点
- **文档永不过期**（"No more stale docs. Ever."）
- 自动生成架构图/类图/时序图
- 每个章节和聊天答案直接链接到源码
- 当前仅支持公开仓库，私有仓库通过 Gemini CLI 扩展（waitlist）

#### 对本项目的对比
- Google 使用**图数据库 + Agentic RAG**，本项目使用 **Leiden 聚类 + 多 Agent 递归**
- Google 强调**实时同步**（PR 触发），本项目使用**状态管理 + 增量生成**
- Google 是云托管产品，本项目是**本地/自托管**开源方案

---

### 2.9 其他 2025-2026 相关工作

| 论文 | 年份 | 核心贡献 |
|------|------|----------|
| **HMCS** (Dhulshette et al.) | 2025.01 | 基于目录结构的层次化模块摘要，使用本地 LLM |
| **Code-Craft** (arXiv 2504.08975) | 2025.04 | 层次图摘要（HCGS），自底向上从代码图构建结构化摘要，增强 RAG 检索上下文 |
| **ICCSA'25 Hierarchical Summarization** | 2025.07 | 层次化摘要用于代码搜索和 Bug 定位 |
| **Automated & Context-Aware** (ACL INLG 2025) | 2025 | 上下文感知 Javadoc 生成 |
| **DeepWiki** (Devin/Cognition) | 2025 | 商业闭源产品 + OpenDeepWiki 开源替代 |
| **HCAG** (arXiv 2603.20299) | 2026.03 | 层次抽象 + RAG，面向理论驱动的代码库（AGT），递归解析 + 规划式生成 |
| **RepoRepair** (arXiv 2603.01048) | 2026.03 | 反向验证：用 LLM 生成的文档辅助仓库级自动程序修复，证明文档是下游任务的有效中间表示 |
| **Code vs AST Inputs** (arXiv 2602.06671) | 2026.02 | 实证：AST 序列化输入 vs 原始代码对 LLM 摘要质量影响相当，但 AST 可缩短 28.6% 输入长度 |
| **StackRepoQA** (arXiv 2603.26567) | 2026.03 | 仓库级 QA 基准，评估 LLM 在仓库层面问答的能力 |

---

## 3. 技术路线对比与分类

### 3.1 按文档组织策略分类

| 策略 | 代表工作 | 优点 | 缺点 |
|------|----------|------|------|
| **目录树结构** | HMCS | 直观，与文件系统对齐 | 功能跨文件时组织混乱 |
| **依赖图拓扑序** | RepoAgent, DocAgent | 确保依赖完整性 | 粒度受限于函数/类级 |
| **功能特性聚类** | RepoSummary, HGEN | 语义聚合，贴近开发者认知 | 聚类结果可能与预期不符 |
| **递归模块树** | CodeWiki(论文), 本项目 | 灵活层级，自适应深度 | 树结构设计依赖 LLM 判断 |

### 3.2 按聚类/分区算法分类

| 算法 | 使用论文 | 特点 |
|------|----------|------|
| **Leiden 社区发现** | RepoSummary, **本项目** | 大规模稀疏图上效果好，支持分辨率调节 |
| **多算法共识投票** | HGEN | 5 种算法投票降低单一偏差，但计算成本高 |
| **LLM 驱动分区** | CodeWiki(论文) | 语义理解强，但不可控且有幻觉风险 |
| **无聚类（拓扑序）** | RepoAgent, DocAgent | 简单确定性，但无法识别功能模块 |

### 3.3 按 Agent 架构分类

| 架构 | 代表工作 | 特点 |
|------|----------|------|
| **单 Agent + 工具** | RepoAgent | 最简架构，依赖确定性工具提供上下文 |
| **多专职 Agent 协作** | DocAgent | Reader/Searcher/Writer/Verifier 专业分工 |
| **递归委派 Agent** | CodeWiki(论文), **本项目** | Agent 自主决定拆分，支持任意深度 |
| **无 Agent（流水线）** | HGEN, RepoSummary | 确定性管道，可复现但缺乏自适应 |

---

## 4. 关键技术挑战与现有解法

### 4.1 上下文窗口限制

这是所有仓库级方法面临的**最核心挑战**。

| 解法 | 论文 | 说明 |
|------|------|------|
| 拓扑排序 + 增量上下文 | RepoAgent, DocAgent | 利用已生成的子文档替代原始代码 |
| 方法摘要替代源码 | RepoSummary | Sentence-BERT 压缩 → 用摘要替换超长方法 |
| 动态委派分治 | CodeWiki(论文) | 超阈值模块自动委派子 Agent |
| Leiden 预聚类 + auto-split | **本项目** | 预先将超大 cluster 拆分（>1000 组件） |
| 迭代截断 + 签名降级 | **本项目** | head(60%)+tail(40%) 截断，最终降级为函数签名 |
| 上下文溢出自动重试 | **本项目** | 检测 400 错误，每次裁剪 100K tokens 重试 |

### 4.2 文档质量保证

| 解法 | 论文 | 说明 |
|------|------|------|
| Verifier 回环 | DocAgent | Verifier 可触发重写或新一轮信息收集 |
| 双重去重 | HGEN | 聚类阶段 + 追溯阶段两次去重 |
| 跨模块引用注册表 | CodeWiki(论文) | 全局注册避免重复 |
| Mermaid 语法验证 | **本项目** | mermaid_validator 确保图表可渲染 |

### 4.3 评估方法

| 方法 | 论文 | 说明 |
|------|------|------|
| 人工偏好测试 | RepoAgent | 盲测 vs 人工文档，简单但主观 |
| Completeness/Helpfulness/Truthfulness | DocAgent | 三维框架 + AST 验证 + LLM-as-judge |
| 概念覆盖率 + 追溯 mAP | HGEN | 对照人工标注的概念和链接 |
| Feature Coverage + Traceability Recall | RepoSummary | 量化功能覆盖和方法级追溯 |
| CodeWikiBench (层次 rubric) | CodeWiki(论文) | 首个仓库级基准，多模型 Judge |

---

## 5. 与本项目 CodeWiki 的定位对比

| 维度 | 本项目 | 最接近的论文方案 | 差异 |
|------|--------|------------------|------|
| **聚类算法** | Leiden 社区发现 + auto-split | RepoSummary（同 Leiden） | 本项目在依赖图上聚类；RS 用邻接+语义混合矩阵 |
| **Agent 框架** | pydantic-ai 多 Agent，递归委派 | CodeWiki 论文（递归委派） | 本项目有严格类型安全 + 状态管理 |
| **文档组织** | 层次模块树，自底向上合成 | CodeWiki 论文（模块树） | 架构高度相似 |
| **token 管理** | 4 层截断 + 签名降级 + 溢出重试 | 论文方案中无如此细粒度的管理 | **本项目更完善** |
| **增量更新** | 有状态管理 + 缓存 | RepoAgent（Git hook） | 方法不同：本项目用 GenerationState，RA 用 Git hook |
| **评估体系** | 无正式基准 | CodeWiki 论文（CodeWikiBench） | **可借鉴** |
| **语言支持** | 多语言（依赖 Tree-Sitter） | CodeWiki 论文（7 语言） | 本项目理论上也支持多语言 |
| **可视化** | Mermaid 图 + Bulma CSS 前端 | CodeWiki 论文（Mermaid） | 本项目有完整的静态站点生成 |

### 本项目的独特优势

1. **Leiden + auto-split 的预处理聚类**：在运行 Agent 之前就将代码库划分为合理大小的 cluster，比论文方案的运行时动态委派更可预测、更高效
2. **4 层渐进式 token 预算管理**：从文件内容截断 → 签名降级 → 紧凑元数据 → 硬截断，是目前论文中最精细的 token 管理策略
3. **上下文溢出自动重试**：检测模型 400 错误后自动裁剪 100K 重试，论文中无类似机制
4. **完整的生产级管道**：包含缓存、状态持久化、优雅关闭、增量生成、日志系统等生产级特性
5. **前端渲染**：静态 HTML 站点（Bulma CSS + Mermaid + KaTeX），论文方案多数只输出 Markdown

### 可以从论文中借鉴的方向

1. **评估体系**：借鉴 CodeWikiBench 的层次化 rubric + 多 Agent Judge 方法
2. **Verifier 回环**：借鉴 DocAgent 的质量验证机制，在生成后自动检查文档质量
3. **功能特性视角**：借鉴 RepoSummary 的 Feature-oriented 组织，在目录结构之外提供功能视图
4. **三维质量指标**：借鉴 DocAgent 的 Completeness/Helpfulness/Truthfulness 评估框架
5. **自动增量更新**：借鉴 RepoAgent 的 Git hook 驱动机制

---

## 6. 研究趋势总结（2024 → 2026）

1. **从函数级到仓库级**：2024 年以来研究焦点明确转向仓库级整体文档
2. **多 Agent 协作成为主流**：DocAgent（ACL 2025）和 CodeWiki(论文) 都采用多 Agent 架构
3. **聚类/分区是核心分歧点**：Leiden、多算法共识、LLM 驱动、拓扑序等不同策略各有优劣
4. **评估方法仍在探索**：从 BLEU/ROUGE → LLM-as-judge → 层次化 rubric，尚无统一标准
5. **实际部署面临挑战**：token 成本、生成一致性、增量更新、幻觉控制等问题仍未完全解决
6. **开源 vs 闭源 vs 平台化竞争**：Google Code Wiki（平台级）、DeepWiki（闭源）、CodeWiki/OpenDeepWiki（开源）三足鼎立
7. **文档从"目的"变成"手段"**（2026 新趋势）：RepoRepair 证明 LLM 生成的文档可作为中间表示辅助下游任务（程序修复），文档不再仅面向人类
8. **知识图谱融合**（2026 新趋势）：Google Code Wiki 使用图数据库 + Agentic RAG，HCAG 使用层次化知识图谱，代码文档正在与知识图谱技术融合
9. **认知科学视角介入**（2026 新趋势）：CodeMap（ICPC 2026）从审计师认知流程出发设计系统，强调"认知对齐"而非纯技术优化
10. **架构视图生成仍是开放问题**（2026 新发现）：ArchView 的大规模实证揭示 LLM 存在根本性的"粒度失配"——在代码层级思考，难以自发提升到架构层级

---

## 参考文献

### 核心论文（详细分析）
1. Luo, Q. et al. (2024). *RepoAgent: An LLM-Powered Open-Source Framework for Repository-level Code Documentation Generation.* arXiv:2402.16667
2. Dearstyne, K. et al. (2024). *Supporting Software Maintenance with Dynamically Generated Document Hierarchies (HGEN).* arXiv:2408.05829
3. Yang, D. et al. (2025). *DocAgent: A Multi-Agent System for Automated Code Documentation Generation.* ACL 2025. arXiv:2504.08725
4. Zhu, Y. et al. (2025). *RepoSummary: Feature-Oriented Summarization and Documentation Generation for Code Repositories.* arXiv:2510.11039
5. Nguyen, H.A. et al. (2025). *CodeWiki: Automated Repository-Level Documentation at Scale.* arXiv:2510.24428
6. Gao, J. et al. (2025→2026). *CodeMap: Understanding Codebase like a Professional! Human-AI Collaboration for Code Comprehension.* ICPC 2026. arXiv:2504.04553
7. Miryala, S. et al. (2026). *LLM-based Automated Architecture View Generation: Where Are We Now? (ArchView)* arXiv:2603.21178

### 工业产品
8. Google. (2025). *Introducing Code Wiki: Accelerating your code understanding.* codewiki.google
9. Cognition / Devin. (2025). *DeepWiki.* deepwiki.com（闭源）+ OpenDeepWiki（开源替代）

### 辅助论文
10. Dhulshette, N. et al. (2025). *Hierarchical Repository-Level Code Summarization for Business Applications Using Local LLMs.* arXiv:2501.07857
11. Code-Craft (2025). *Hierarchical Graph-Based Code Summarization for Enhanced Context Retrieval.* arXiv:2504.08975
12. *Automated and Context-Aware Code Documentation Leveraging Advanced LLMs.* ACL INLG 2025. arXiv:2509.14273
13. Wu, Y. & Deng, X. (2026). *HCAG: Hierarchical Abstraction and Retrieval-Augmented Generation on Theoretical Repositories with LLMs.* arXiv:2603.20299
14. Pan, Z. et al. (2026). *RepoRepair: Leveraging Code Documentation for Repository-Level Automated Program Repair.* arXiv:2603.01048
15. Dong, S. et al. (2026). *Code vs Serialized AST Inputs for LLM-Based Code Summarization.* arXiv:2602.06671
16. StackRepoQA (2026). *Benchmarking LLMs on Repository-Level Question Answering.* arXiv:2603.26567
