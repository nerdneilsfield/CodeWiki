# Tree Refinement Before Documentation Generation

## Background

CodeWiki's current module documentation flow mixes two different responsibilities:

1. Build the module tree
2. Generate documentation from that tree

Today, top-level clustering happens before documentation generation, but deeper sub-module
splits are still produced inside the documentation agents. That creates several structural
problems:

- The scheduler builds its initial work queue from an incomplete tree
- Parent modules can be generated before their child modules exist
- Sub-modules are added to `module_tree.json` during documentation generation
- Fill-pass has to compensate for missing child docs and re-run parents later
- Cache validity is unstable because the structure of a task changes during generation
- `max_depth` currently limits runtime recursive splitting, not the final frozen tree depth

Observed symptom pattern:

- Initial queue logs show top-level modules treated as leaves
- Sub-module generation happens later inside agent tool calls
- Parents are re-generated after children appear
- Interrupted runs reset `running` entries to `stale`, amplifying repeated parent work

This is the wrong boundary. The tree must be complete before documentation generation starts.

## Goal

Move all structure-building work into a dedicated tree refinement phase, so the pipeline becomes:

1. Graph build
2. Index build
3. Top-level clustering
4. Recursive tree refinement with LLM assistance until `max_depth`
5. Freeze `module_tree.json`
6. Generate leaf docs
7. Generate parent docs bottom-up
8. Generate root overview
9. Generate guides
10. Postprocess
11. Static HTML generation

## Non-Goals

- Do not remove LLM assistance from clustering or refinement
- Do not make module naming purely heuristic
- Do not make parent docs regenerate wholesale for every small child change
- Do not redesign the markdown/html rendering pipeline in this change

## Design Principles

### 1. Tree first, docs second

No documentation agent may mutate the module tree. All splitting, regrouping, naming,
path assignment, and description generation must finish before any module doc is written.

### 2. Freeze structure aggressively

After tree refinement finishes:

- `module_tree.json` becomes immutable for the rest of the run
- documentation agents may read it, but never edit it
- child generation tools must no longer add nodes to the tree

### 3. Incremental updates are bottom-up

Change detection should start at leaves and propagate upward:

- leaves decide whether they changed based on component changes
- parents decide whether they changed based on direct child changes
- overview docs keep segment-level updates rather than full regeneration by default

### 4. Reuse identity before recomputing names

When cluster/refinement output changes, the system should first ask:

"Which new nodes are still the same old nodes?"

Only after attempting identity reuse should it rename or re-path nodes.

## Proposed Pipeline

### Stage 1: GraphBuildStage

Unchanged responsibility:

- build components
- build leaf nodes

### Stage 2: IndexBuildStage

Unchanged responsibility:

- build symbol table, edge index, glossary, link map inputs

### Stage 3: ClusteringStage

Responsibility:

- produce top-level module groups only
- apply the same identity reuse rules used by subtree refinement so top-level
  title/path/module_id stability follows one mechanism, not a separate freeze rule

Output:

- top-level `module_tree`
- one level only

### Stage 4: TreeRefinementStage

New dedicated stage.

Responsibility:

- recursively refine each parent node into child nodes
- use LLM assistance for split decisions, naming, path generation, and descriptions
- stop recursion at `max_depth`
- stop recursion early when split criteria are not met
- assign collision-free `_doc_filename` values
- write the final frozen `module_tree.json`

This stage replaces runtime sub-module generation as a tree-building mechanism.

### Stage 5: StateInitStage

Responsibility:

- initialize cache entries from the final frozen tree
- assign output files for every leaf and parent task
- register dependency edges for bottom-up generation

### Stage 6: ModuleGenerationStage

Responsibility:

- generate leaf docs first
- then generate parent docs bottom-up
- never mutate the tree

### Stage 7: RootOverviewGenerationStage

Responsibility:

- generate repository/root overview after all module docs are available

### Stage 8: GuideGenerationStage

Responsibility:

- generate guides from the frozen tree and completed docs

### Stage 9: PostprocessStage

Responsibility:

- run link rewrite, math/mermaid repair, formatting

### Stage 10: StaticHTMLStage

Responsibility:

- render final static site from the docs directory

## Tree Refinement Stage

### Input

For each parent node:

- parent title/path/module_id if already known
- direct component membership
- current depth
- dependency/index context
- previous tree snapshot for identity reuse

### Output

Each node in the frozen tree must include:

- `module_id`
- `title`
- `path`
- `description`
- `components`
- `children`
- `_doc_filename`

`_doc_filename` is assigned by `TreeRefinementStage`, not `StateInitStage`.
It must be frozen together with `module_id/title/path/description`.

Filename assignment must remain collision-safe against:

- filenames already assigned earlier in the current refinement pass
- filenames already present in cache registry `output_file` mappings from previous runs

### LLM Responsibilities

The LLM may assist with:

- deciding whether a parent should split
- proposing child groups
- naming child groups
- writing parent and child descriptions
- refining path semantics when a new child is created

The LLM may not:

- create tree mutations after the refinement stage ends

### Recursion Rule

Refinement recurses until one of these conditions stops it:

- current depth reached `max_depth`
- node does not meet split criteria
- node is structurally simple enough to remain a leaf

A node is considered structurally simple enough to remain a leaf when either of
these is true:

- `len(components) < min_components_for_split`
- `distinct_file_count < min_distinct_files_for_split`

## Split Criteria

Tree refinement should use dedicated split criteria, separate from doc-generation thresholds.

Recommended configuration keys:

```toml
[refinement]
max_depth = 3
min_components_for_split = 6
min_distinct_files_for_split = 4
max_cluster_components = 1000
```

Notes:

- `max_depth` limits final tree depth
- refinement split thresholds are intentionally not adaptive; subtree decisions
  should stay local and predictable rather than depending on repository-global scale
- `min_components_for_split` prevents splitting tiny groups with too little semantic mass
- `min_distinct_files_for_split` prevents splitting groups that do not span enough files
- `max_cluster_components` remains the oversized-cluster safeguard

These rules apply during tree refinement, not during module doc generation.

## Frozen Tree Schema

Example:

```json
{
  "Backend Services & Integrations": {
    "module_id": "services_mcp",
    "title": "Backend Services & Integrations",
    "path": "services_mcp",
    "description": "Background services, API communication, MCP integration, and analytics support.",
    "_doc_filename": "services_mcp.md",
    "components": ["..."],
    "children": {
      "API Communication": {
        "module_id": "api_communication",
        "title": "API Communication",
        "path": "api_communication",
        "description": "Client lifecycle, retry behavior, logging, and query-facing request orchestration.",
        "_doc_filename": "backend_services_and_integrations-api_communication.md",
        "components": ["..."],
        "children": {}
      }
    }
  }
}
```

Descriptions are part of the frozen tree and may be reused later by:

- parent doc segment generation
- navigation rendering
- guide generation
- static site grouping/fallbacks

## Identity Reuse Strategy

### Why reuse matters

When clustering changes slightly, path/title churn destroys navigability and makes cache reuse poor.
The system should prefer stable node identity whenever the new node is still "mostly" the old node.

### Configurable threshold

```toml
[refinement]
identity_reuse_threshold = 0.70
```

Default:

- `identity_reuse_threshold = 0.70`

### Matching strategy

For each new child under a parent:

1. Try exact component-set match with old siblings
2. Otherwise compute overlap against old siblings
3. Reuse old identity only if the best match clears the threshold and is dominant

Suggested metrics:

- `overlap_ratio = |new ∩ old| / |new|`
- `jaccard = |new ∩ old| / |new ∪ old|`

Reuse should happen when:

- exact component set match, or
- `overlap_ratio >= identity_reuse_threshold`
- and `overlap_ratio - second_best_overlap >= 0.15`

Here:

- `best_overlap` is the best candidate old sibling for the new node
- `second_best_overlap` is the next-best candidate old sibling
- if there is no second-best candidate, treat `second_best_overlap = 0`

### What gets reused

If identity is reused, preserve:

- `module_id`
- `path`
- `title`

Descriptions may either:

- be preserved as-is, or
- be lightly refreshed if the node still matches but meaning shifted somewhat

### Split / merge handling

#### Split

If one old node becomes several new nodes:

- the dominant successor inherits the old identity
- other new nodes get fresh ids/paths/titles

The dominant successor is the new node with the highest old-node overlap,
measured as:

```text
split_successor_overlap = |new ∩ old| / |old|
```

and it only inherits when:

- `split_successor_overlap >= identity_reuse_threshold`
- and `split_successor_overlap - second_best_successor_overlap >= 0.15`

#### Merge

If several old nodes become one new node:

- reuse old identity only if one old node is clearly dominant
- otherwise create a new identity

For merge, dominance is measured in the opposite direction from split:

```text
merge_predecessor_overlap = |new ∩ old| / |new|
```

This asks:

"How much of the new node is explained by this old predecessor?"

The dominant predecessor inherits identity only when:

- `merge_predecessor_overlap >= identity_reuse_threshold`
- and `merge_predecessor_overlap - second_best_predecessor_overlap >= 0.15`

This asymmetry is intentional:

- split uses `/ |old|` because it asks how much of the old node is captured by a new successor
- merge uses `/ |new|` because it asks how much of the new node comes from an old predecessor

## Incremental Change Propagation

### Leaf rerun threshold

Config:

```toml
[incremental]
leaf_rerun_threshold = 0.30
```

Default:

- `leaf_rerun_threshold = 0.30`

Rule:

```text
changed_component_ratio =
  changed_components / total_components
```

If the ratio meets or exceeds the threshold, regenerate the leaf doc.

### Parent rerun threshold

Config:

```toml
[incremental]
parent_rerun_threshold = 0.30
```

Default:

- `parent_rerun_threshold = 0.30`

Rule:

```text
changed_direct_child_ratio =
  changed_direct_children / total_direct_children
```

Important:

- parent uses direct child ratio only
- do not dilute over the entire descendant subtree

### Hard rerun triggers

These bypass the ratio thresholds:

- child added
- child removed
- child title changed
- child path changed
- child membership changed enough to fail identity reuse

These are structural changes and should force parent refinement/doc updates.

## Parent Document Segments

Parent docs should support partial regeneration.

Initial segment set:

- `opening/summary`
- `overview/architecture`
- `per-child summary blocks`

Each parent segment is its own cache artifact:

```text
module:{doc_id}:segment:opening
module:{doc_id}:segment:overview
module:{doc_id}:segment:child:{child_doc_id}
```

Segment files are stored under:

```text
.codewiki/_module_parts/{doc_stem}/opening.md
.codewiki/_module_parts/{doc_stem}/overview.md
.codewiki/_module_parts/{doc_stem}/child_{child_doc_stem}.md
```

Mapping note:

- `child_doc_id` is the stable artifact identity
- `child_doc_stem = os.path.splitext(child._doc_filename)[0]`
- artifact IDs and segment file paths are related, but they are not interchangeable

Input hashes:

- `opening/summary`
  - `stable_hash([title, path, description, output_language, PROMPT_VERSION])`
- `overview/architecture`
  - `stable_hash([title, path, description, direct_child_ids..., direct_child_input_hashes..., output_language, PROMPT_VERSION])`
- `per-child summary block`
  - `stable_hash([child.module_id, child.title, child.path, child.description, child.input_hash, output_language, PROMPT_VERSION])`

Parent assembled doc input hash:

```text
stable_hash([
  opening_hash,
  overview_hash,
  child_segment_hash_1,
  child_segment_hash_2,
  ...,
  output_language,
  PROMPT_VERSION,
])
```

Behavior:

- if parent change ratio is below threshold, only stale segments update
- if it exceeds threshold, force-invalidate all segment artifacts for that parent,
  regenerate every segment, and then reassemble the parent doc coherently

This extends the current overview segmented regeneration idea to parent module docs too.

## Documentation Generation Rules

### Leaf generation

Generate all leaves first from the frozen tree.

Each leaf doc uses:

- frozen title/path/description
- frozen `_doc_filename`
- frozen membership

### Parent generation

Generate parents bottom-up only after all direct child docs are available.

Parent prompts may consume:

- child descriptions from the frozen tree
- child doc summaries
- child filenames

But they may not create new children or rename existing ones.

### Root overview generation

Only after all module docs are complete.

Root overview continues to use the existing overview-parts model (`arch_intro` +
per-child parts). It is conceptually similar to parent segment updates, but it
remains a separate implementation path in this spec to reduce migration risk.

### Guides

Run after the full tree and doc set is stable.

## Cache Semantics

### Refinement cache artifacts

Tree refinement results must be cached explicitly, because this stage contains
LLM calls and should not be recomputed unnecessarily.

Artifact type:

```text
refinement:{doc_id}
```

Output path:

```text
.codewiki/_refinement/{normalized_doc_id}.json
```

Input hash:

```text
stable_hash([
  sorted component ids,
  sha256(component.source_code) for each selected component,
  current_depth,
  max_depth,
  min_components_for_split,
  min_distinct_files_for_split,
  max_cluster_components,
  identity_reuse_threshold,
  output_language,
  REFINEMENT_PROMPT_VERSION,
])
```

Output:

- frozen subtree payload rooted at `doc_id`
- including title/path/module_id/description/_doc_filename/children

Refinement cache invalidates when:

- component membership changes
- any member component source hash changes
- refinement config changes
- output language changes
- refinement prompt version changes

### What changes

Cache keys become stable because tree structure is frozen before doc generation.

That means:

- leaf task meaning no longer changes during the run
- parent doc cache entries can safely depend on direct child artifacts
- fill pass no longer needs to compensate for normal tree mutation

Parent doc input hash must be defined as:

```text
see "Parent assembled doc input hash" in §Parent Document Segments
```

### What should be removed

These behaviors should disappear:

- runtime tree mutation by documentation agents
- parent-first generation followed by child-triggered parent rework
- fill-pass as a normal mechanism for discovering new children

Fill pass should remain only as:

- retry for failed/cancelled tasks
- crash recovery completion

Not as:

- primary structure completion logic

### Resume semantics

Resume behavior differs from normal incremental-change evaluation.

If interruption happens before tree refinement completes:

- resume tree refinement first, using `refinement:*` cache entries
- only unfinished refinement nodes (`missing`, `failed`, `running -> stale`) re-enter work

If interruption happens after the tree is frozen:

- resume documentation generation strictly as `leaf -> parent -> root`
- first complete unfinished leaf docs
- then enqueue unfinished parents whose direct children are all valid
- already-valid parents do not re-run merely because some leaf docs were completed during resume

In other words:

- resume restores the unfinished frontier
- incremental rerun logic handles code changes
- the two behaviors are intentionally separate

## Migration Strategy

### Phase 1: Introduce TreeRefinementStage

- move recursive split logic out of `generate_sub_module_documentation`
- build final tree before `StateInitStage`

### Phase 2: Freeze tree mutations

- remove tree mutation from documentation agents
- make child-generation tool doc-only or remove it entirely

### Phase 3: Rebuild scheduling around frozen tree

- leaf-first queue from final tree
- parent queue derived from frozen child dependencies

### Phase 4: Extend incremental rules

- add configurable leaf and parent rerun thresholds
- add identity reuse threshold
- add parent segment updates

### Phase 5: Remove fill-pass dependency on tree discovery

- fill pass becomes failure-recovery only

### Schema migration

This design changes cache semantics enough to require a schema bump.

- bump cache registry schema version
- old refinement-less cache entries are not trusted for refinement reuse
- on first run after migration, refinement entries start fresh
- module/guide/postprocess entries may be reused only if their new input-hash
  definitions still match; otherwise they become stale

### Orphan cleanup

Cleanup is intentionally layered.

#### A. Internal cache artifacts

These may be cleaned aggressively by reconciling registry state with the frozen tree:

- `.codewiki/_module_parts/...`
- `.codewiki/_refinement/...`

They are internal intermediate artifacts, so deleting stale entries only causes
regeneration on the next run.

#### B. User-visible outputs

Visible docs are cleaned conservatively.

Only one case is auto-cleaned:

- the same `artifact_id` previously owned output file `X`
- after refinement / identity reuse / rename, that same `artifact_id` now owns `Y`
- therefore `X` is a known orphan from a rename event

In that case:

- `X.md` is a deletion candidate
- `X.html` is also a deletion candidate

Files are **not** auto-deleted when:

- the registry has no owner for them
- ownership did not actually move

#### User-modified old outputs

If a rename-candidate old file `X.md` appears to have been manually modified since
it was last generated:

- do not delete it
- keep the file in place
- emit a degraded warning
- continue the run

## Risks

### Risk 1: Tree refinement becomes expensive

Mitigation:

- keep split thresholds conservative
- cache subtree refinement results by input hash
- stop recursion early on trivially small groups

### Risk 2: Naming churn on moderate membership changes

Mitigation:

- use identity reuse threshold
- preserve title/path/id when dominant overlap is high

### Risk 3: Parent partial updates become incoherent

Mitigation:

- use segment-based updates only below threshold
- force-invalidate all segments and rewrite them coherently once the parent
  change ratio crosses the configured limit

## Acceptance Criteria

The redesign is successful when all of these are true:

1. `module_tree.json` is fully built before module doc generation starts
2. No documentation agent mutates the tree
3. Initial scheduler queue reflects the final tree
4. Leaf docs generate before their parents
5. Parent docs are generated exactly once in the normal successful case
6. Fill pass only retries failures/cancellations; it does not discover normal children
7. Incremental reruns follow the configured `leaf` and `parent` thresholds
8. Cluster/title/path reuse is stable when overlap remains high
9. `max_depth` controls final tree depth, not runtime agent recursion
10. Resume restores only unfinished work; already-valid parents are not regenerated during resume
11. In the normal successful path, `parent_artifact.attempt_count == 1`
