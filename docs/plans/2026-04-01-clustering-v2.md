# Clustering v2 Implementation Plan

**Date:** 2026-04-01
**Scope:** 图算法定骨架 + LLM 只命名 + schema 校验 + 确定性
**Depends on:** Graph Layer v1 (commit f746d3f, 205 tests)
**Spec:** docs/v3.md sections 3.3, 4.3, 5.1-5.4

---

## 与 v3.md 的对齐映射

| v3.md 规格 | 位置 | 本 plan 对应 |
|-----------|------|------------|
| 三段式聚类：目录先验→SCC→社区发现 | 4.3 L331-336 | Phase 3: partitioner.py |
| LLM 只做命名/简介/少量边界微调 | 3.3 L176, 4.3 L336-339 | Phase 4: naming.py |
| 输入特征：图结构+目录先验+文本语义 | 5.1 L469-480 | Phase 2: graph_builder.py |
| 边权重策略：imports/extends 高，calls 中 | 5.1 L472 | Phase 2: WEIGHT_MAP |
| 模块树 schema v2（固定 JSON） | 5.2 L488-521 | Phase 1: models.py ModuleNode |
| members.symbols + members.files | 5.2 L502-504 | Phase 1: ModuleNode.members |
| evidence_refs + constraints | 5.2 L506-511 | Phase 1: ModuleNode 字段 |
| module_id 由成员 hash 生成（稳定） | 5.2 L525 | Phase 1: module_id_from_members() |
| title 中英并列 | 5.2 L526 | Phase 4: naming prompt |
| path 唯一可预测 | 5.2 L527 | Phase 1: validate_tree() |
| members 字典序排列 | 5.2 L528 | Phase 1: canonicalize_tree() |
| 确定性：固定种子+稳定排序 | 4.3 L344-347 | Phase 3: seed=42 + sorted() |
| schema 校验 + canonicalization | 4.3 L347 | Phase 1: validate_tree() |
| 质量评估：内凝聚/间耦合 | 5.3 L536 | Phase 5: cluster_quality() |
| VALIDATE_TREE 伪代码 | 5.4 L588-600 | Phase 1: validate_tree() |
| 回退到 v1 | 4.3 L351 | Phase 5: index_products=None fallback |

---

## 聚类单位与 symbol/component 映射

**聚类单位是 component（与 v1 一致）。** component_id 是 DocumentationGenerator 的原子单位。

映射关系：
- 一个 component_id（如 `src/auth/handler.py::AuthHandler`）对应 1~N 个 symbol_id
- SymbolTable 通过 `by_file()` + name 匹配可查出 component 对应的 symbols
- 聚类算法、module_id 计算、validate_tree 全部以 **component_id** 为主语义

### 填充规则

| 字段 | 内容 | 来源 |
|------|------|------|
| `members.components` | 聚类分配的 component_ids（字典序） | 聚类算法直接输出 |
| `members.symbols` | 该簇所有 component 对应的 symbol_ids（字典序） | 由 component→symbol 映射批量填充 |
| `members.files` | 该簇所有 component 的唯一文件路径（字典序） | 从 component.relative_path 去重 |
| `module_id` | `sha256("|".join(sorted(component_ids)))[:12]` | 以 component_ids 为真值 |

### validate_tree 对齐

v3.md L590-598 的 "every symbol appears at most once" 在实现中等价于
**"every component appears in exactly one module"**，因为 component→symbol 是确定映射，
component 不重复则 symbol 不重复。

### to_legacy_dict 映射

`members.components` 直接写入 legacy `"components"` 字段，无歧义。

---

## 数据模型（对齐 v3.md 5.2 schema L488-521）

```python
class ModuleMembers(BaseModel):
    components: list[str] = []  # component_ids, 字典序 — 聚类主语义
    symbols: list[str] = []     # symbol_ids, 字典序 (v3.md L503)
    files: list[str] = []       # file paths, 字典序 (v3.md L504)

class ModuleConstraints(BaseModel):
    public_api_symbols: list[str] = []           # (v3.md L509)
    boundary_edges: list[dict] = []              # (v3.md L510)

class ModuleNode(BaseModel):
    module_id: str              # component_ids hash, 稳定 (v3.md L525)
    title: str                  # 展示名，中英并列 (v3.md L526)
    path: str                   # 文档路径，唯一可预测 (v3.md L527)
    description: str = ""
    aliases: list[str] = []     # 旧名/同义名 (v3.md L343)
    members: ModuleMembers
    evidence_refs: list[SourceRange] = []        # (v3.md L506)
    constraints: ModuleConstraints = ModuleConstraints()
    children: list["ModuleNode"] = []
    extra_top_level_modules: list[dict] = []     # 仅 root 节点使用 (v3.md L514-518)

# 顶层容器
class ModuleTree(BaseModel):
    schema_version: str = "codewiki.module_tree.v2"  # (v3.md L490)
    generated_from: dict = {}    # {"commit": str, "index_version": str} (v3.md L491)
    root: ModuleNode             # 单根节点，children+extra_top_level_modules 都在里面 (v3.md L492-518)
```

### Legacy 兼容

`to_legacy_dict()` 输出 v1 格式：
```python
{
  "模块名": {
    "path": "...",
    "components": ["comp_id_1", ...],  # 直接来自 members.components
    "children": { ... }
  }
}
```

---

## Phase 1: ModuleNode 模型 + 树校验

**文件:** `codewiki/src/be/clustering/models.py`

对齐 v3.md 5.2 (schema) + 5.4 (VALIDATE_TREE 伪代码 L588-600):

1. `ModuleNode`, `ModuleMembers`, `ModuleConstraints`, `ModuleTree` — Pydantic 模型
2. `module_id_from_members(component_ids)` — `sha256("|".join(sorted(component_ids)))[:12]`
3. `validate_tree(tree, all_component_ids)` — 对齐 v3.md L588-600 VALIDATE_TREE:
   - 每个 component 最多出现在一个模块叶子中（L591 check_uniqueness）
   - 所有 `all_component_ids` 被分配到某个模块（L592 check_public_api_coverage）
   - path 全局唯一（L593 check_path_uniqueness）
   - module_id 全局唯一
   - 树无环（children 不能引用祖先）
   - extra_top_level_modules 存在（L594 check_required_modules）
4. `canonicalize_tree(tree)` — 排序 members + children（v3.md L528, L347）
5. `to_legacy_dict()` — 转为 v1 格式供 DocumentationGenerator 消费

**测试:** `tests/test_clustering_models.py`
- module_id 确定性
- validate_tree 检测重复/缺失/路径冲突
- canonicalize 排序稳定性
- legacy dict 格式正确
- round-trip: ModuleTree → JSON → ModuleTree

---

## Phase 2: 加权图构建（对齐 v3.md 5.1）

**文件:** `codewiki/src/be/clustering/graph_builder.py`

对齐 v3.md 5.1 L472 边权重策略：

```python
WEIGHT_MAP = {
    (EdgeType.IMPORTS, None):           1.0,  # 高权重 (v3.md L472)
    (EdgeType.EXTENDS, None):           1.0,  # 高权重
    (EdgeType.CALLS, Confidence.HIGH):  0.5,  # 中权重，分置信度
    (EdgeType.CALLS, Confidence.MEDIUM):0.3,
    (EdgeType.CALLS, Confidence.LOW):   0.2,
}
CO_LOCATION_WEIGHT = 0.3
MAX_EDGE_WEIGHT = 3.0  # 累加上限
```

`build_clustering_graph(index_products, component_ids, components)`:
1. 从 EdgeIndex 获取所有 symbol→symbol 边
2. 映射到 component_id→component_id（symbol 所在的 component）
3. 按类型+置信度计算权重，同对累加（cap 3.0）
4. 同文件 component 加 co-location 边
5. 返回 `nx.Graph`（无向加权）

**测试:** `tests/test_clustering_graph_builder.py`
- 权重计算正确
- 累加 cap 生效
- co-location 边
- 孤立节点保留
- unresolved 边不入图

---

## Phase 3: 分区器（对齐 v3.md 4.3 三段式）

**文件:** `codewiki/src/be/clustering/partitioner.py`

### Step 1: 目录先验（v3.md L333）
`partition_by_directory(component_ids, components)`:
- 按顶层包目录分组
- 检测 `__init__.py` / `package.json` 包边界

### Step 2: SCC 收缩（v3.md L334）
`contract_sccs(graph, edge_index, component_ids)`:
- 构建有向图，`nx.strongly_connected_components()`
- 收缩每个 SCC 为超级节点（lexicographically first 作为 ID）
- 边权重合并

### Step 3: 社区发现（v3.md L335）
`detect_communities(graph, dir_partitions, seed=42)`:
- 目录先验注入为 intra-partition 强边（weight 2.0）
- `louvain_communities(graph, weight="weight", seed=seed)`
- 小簇合并（<3 members → 最近邻）
- 确定性：固定 seed + 稳定排序（v3.md L346）

### 编排
`partition_components(component_ids, components, index_products, seed=42)`:
- 调用 build_graph → partition_by_dir → contract_sccs → detect_communities
- 展开超级节点 → 返回 `list[list[str]]`

**测试:** `tests/test_clustering_partitioner.py`
- 目录分区
- SCC 收缩正确性
- Louvain 尊重目录先验
- 确定性（5 次运行一致）
- 小仓库直接返回单簇

---

## Phase 4: LLM 命名（对齐 v3.md 3.3 L176, 4.3 L336-339）

**文件:** `codewiki/src/be/clustering/naming.py`

**Scope cut（有意缩小）：** v3.md L339 允许 LLM 对少量跨簇高耦合边提出
merge/split 建议（需给证据）。**v2 首版禁用边界微调**，LLM 仅做命名和描述。
原因：先保证确定性和可验证性，边界微调需要额外的 schema 和回退逻辑，留待后续开启。

### Prompt（v3.md L337-338，不含 L339 边界调整）
输入：每个簇的成员 component cards + top boundary edges + 目录结构
输出 schema：`[{"cluster_id": str, "title": str, "description": str}]`
明确指令：**不得移动/添加/删除成员，仅命名和描述**

### 降级策略
- LLM 失败/JSON 无效 → 启发式命名（最常见目录名 + 成员类型）
- cluster_id 不匹配 → 缺失的用启发式补上

### title 格式（v3.md L526）
中英并列："认证与权限 (Auth & Access Control)"

**测试:** `tests/test_clustering_pipeline.py`
- mock LLM happy path
- mock LLM 失败降级
- prompt 包含 boundary edges

---

## Phase 5: 集成 + 质量指标

**文件:** `codewiki/src/be/clustering/pipeline.py`

### cluster_modules_v2()
签名兼容 v1 + `index_products` 参数:
```python
def cluster_modules_v2(
    leaf_nodes, components, config,
    index_products: IndexProducts,
    current_module_tree={}, current_module_name=None,
    current_module_path=[], _token_threshold=None,
) -> Dict[str, Any]:
```

流程：
1. 早退检查（token threshold, file count）
2. `partition_components()` → 确定性簇
3. `name_clusters()` → title + description
4. 构建 ModuleNode 树 + `canonicalize_tree()` + `validate_tree()`
5. `to_legacy_dict()` → v1 兼容输出
6. 递归子聚类（同 v1 模式）

### 质量指标（对齐 v3.md 5.3 L536-539）
`cluster_quality(tree, edge_index)`:
- 模块内凝聚：模块内边权 / 模块总边权
- 模块间耦合：跨模块边权 / 总边权
- 记录到日志，不阻断流程

### 接入
- `cluster_modules.py`: 加 `index_products=None` 参数，有则走 v2，无则 v1
- `documentation_generator.py`: 传 `index_products=self.index_products`

**测试:** `tests/test_clustering_pipeline.py`
- 合成仓库 20 组件 4 目录 1 循环 → 全流程
- 确定性（3 次一致）
- 输出格式 = v1 兼容
- index_products=None → v1 fallback

---

## 实施顺序

| 顺序 | Phase | 产出 |
|------|-------|------|
| 1 | Phase 1: models + validation | ModuleNode + 树校验 + legacy 转换 |
| 2 | Phase 2: graph_builder | 加权图 from IndexProducts |
| 3 | Phase 3: partitioner | 目录先验 + SCC + Louvain |
| 4 | Phase 4: naming | LLM 命名 + 降级 |
| 5 | Phase 5: pipeline + integration | 全流程 + 接入 DocumentationGenerator |
| 6 | 全量测试 + commit | 交付 |

---

## 缓存策略

继续沿用现有 commit + component hash 缓存（v1 机制），不在本次改造。
v2 的确定性保证意味着同 input 不需要额外缓存。
