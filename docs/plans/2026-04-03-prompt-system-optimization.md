# Prompt System Optimization

**Date:** 2026-04-03
**Target:** `codewiki/src/be/prompt_template.py` (23 prompts) + `codewiki/src/be/clustering/naming.py`
**Goal:** Reduce AI-flavored output, strengthen structural diversity, improve cross-language consistency, and tighten grounding discipline.

---

## Problem Analysis

Reviewed all 23 prompts + 7 language-specific guides against the de-AI pattern library (PATTERNS_EN.md, PATTERNS_ZH.md, REWRITING_GUIDE.md). Key findings:

### 1. Prompts actively teach AI writing habits

The prompts explicitly instruct the LLM to use patterns that de-AI detectors flag:

| Prompt instruction | De-AI pattern it triggers |
|---|---|
| "Use analogies and metaphors to make abstractions tangible" (line 54) | Produces `tapestry`, `beacon`, `symphony` tier-1 AI vocabulary |
| "think of it as..." / "imagine..." (line 57) | Skeleton repetition: every section opens with "Think of X as Y" |
| Repeated "vivid, jargon-light" (lines 72, 163, 223) | Produces `seamless`, `robust`, `comprehensive` tier-1 words |
| "make it clear, welcoming, and insightful" (line 220) | Vague adjective cluster → AI signal |
| Good example uses "think of it as a 'hot standby' fleet" (line 52) | Model will replicate this pattern across all modules |

### 2. No structural diversity instruction

The core de-AI insight: **detectors flag skeleton repetition, not individual words.** Current prompts give zero guidance on varying sentence structure between sections. The result: every module doc opens with the same "X exists because..." / "Think of X as..." pattern, every overview follows the same "What / Architecture / Decisions / Guide / Workflows" skeleton.

### 3. Output language instructions are weak for non-English

`<OUTPUT_LANGUAGE>` block says "Write ALL documentation content in {language}" but doesn't address:
- Which Chinese register (academic 书面语 vs. technical 技术文档 vs. casual 科普)?
- Whether to translate section headings or keep them English
- How to handle terms without standard Chinese translations

### 4. Duplicate boilerplate across SYSTEM_PROMPT and LEAF_SYSTEM_PROMPT

~60% of the text is identical between the two prompts (OBJECTIVES, WRITING_APPROACH, GROUNDING_RULES). Changes to one must be manually synced to the other.

### 5. Guide prompts lack de-AI constraints entirely

GETTING_STARTED_PROMPT, BEGINNER_SECTION_PROMPT, BUILD_ANALYSIS_PROMPT, ALGORITHM_DEEPDIVE_PROMPT — none have any writing-style diversity instruction. Guide output is consistently the most AI-flavored.

---

## Optimization Plan

### A. Add anti-AI writing discipline block (shared across all doc prompts)

New block `WRITING_DISCIPLINE` to be injected into SYSTEM_PROMPT, LEAF_SYSTEM_PROMPT, REPO_OVERVIEW_PROMPT, MODULE_OVERVIEW_PROMPT, and all guide prompts:

```
<WRITING_DISCIPLINE>
1. **Vary sentence structure.** No two adjacent paragraphs should open with the same pattern. If one starts with a definition, the next should start with a constraint, a scenario, or a counterpoint.

2. **Avoid these overused words and phrases:**
   - delve, tapestry, realm, paradigm, beacon, testament to, robust, comprehensive, cutting-edge, leverage, pivotal, underscores, meticulous, seamless, game-changer, utilize, holistic, actionable, synergy, interplay
   - 值得注意的是, 需要指出的是, 综上所述, 不难发现, 显而易见, 众所周知, 具有重要的理论意义, 具有广阔的应用前景

3. **No empty openers.** Never start a section with "In today's...", "In the ever-evolving...", "In the rapidly changing landscape of...". Start with the specific problem or a direct statement.

4. **Specific over vague — but only when evidence exists.** Replace "significant performance improvement" with the actual number IF the source code, benchmarks, or comments provide it. If no evidence is available, describe the concrete mechanism or symptom instead of inventing numbers. Never fabricate metrics.

5. **Connector words: use when they aid clarity, avoid when redundant.** "therefore", "consequently", "因此", "从而" are fine when they make a non-obvious causal chain explicit. Drop them when the cause-effect is already clear from the previous sentence.

6. **Analogies: one per major section, not per paragraph.** One well-chosen analogy is powerful. Repeating "think of it as..." for every concept makes the text formulaic. After the analogy, switch to concrete code-level explanation.

7. **Prose rhythm.** Mix sentence lengths. Follow a long compound sentence with a short declarative one.
</WRITING_DISCIPLINE>
```

### B. Restructure WRITING_APPROACH to demonstrate variety

Replace the current examples that all use the same "X exists because... think of it as..." pattern. New examples should each use a DIFFERENT opening structure:

```
**Example openings (each uses a different structure — note: no fabricated numbers):**

- Problem-first: "Creating a new TCP connection per query adds measurable latency on every call. `ConnectionPool` amortizes that cost by keeping connections alive between requests."
- Constraint-first: "The downstream parser expects a flat token stream, but raw input contains nested delimiters. `Tokenizer.split()` resolves this with a character-level state machine."
- Question-first: "What happens when two coroutines try to write the same cache entry simultaneously? `LockManager` serializes competing writes through..."
- Consequence-first: "Without rate limiting, a single misbehaving client can saturate the entire API. `RateLimiter` enforces per-client quotas using a token bucket."
```

### C. Strengthen output language instructions

Replace the current thin `<OUTPUT_LANGUAGE>` block with a richer one:

```
<OUTPUT_LANGUAGE>
Write ALL documentation prose in {lang_name}.

- Section headings: prefer {lang_name}, but keep well-known English terms when they are more recognizable in context (e.g., "API Reference", "CLI Commands" may stay as-is if the project's own docs use them)
- Technical terms with no standard translation: keep the English term, optionally add a brief parenthetical explanation on first use
- Code identifiers (function names, class names, variable names): always keep as-is in English
- File paths and CLI commands: keep as-is
- Register: technical documentation — not academic thesis style, not casual blog style
- Avoid mid-sentence language switching unless quoting a code identifier
</OUTPUT_LANGUAGE>
```

### D. Deduplicate SYSTEM_PROMPT and LEAF_SYSTEM_PROMPT

Extract shared blocks into constants. Build prompts by composition:

```python
_SHARED_OBJECTIVES = "..."
_SHARED_WRITING_APPROACH = "..."
_SHARED_GROUNDING_RULES = "..."
_WRITING_DISCIPLINE = "..."

SYSTEM_PROMPT = f"""
<ROLE>...</ROLE>
{_SHARED_OBJECTIVES}
{_SHARED_WRITING_APPROACH}
{_WRITING_DISCIPLINE}
<DOCUMENTATION_STRUCTURE>...complex module specific...</DOCUMENTATION_STRUCTURE>
{_SHARED_GROUNDING_RULES}
<WORKFLOW>...complex module workflow...</WORKFLOW>
<AVAILABLE_TOOLS>...complex module tools...</AVAILABLE_TOOLS>
{{custom_instructions}}
""".strip()
```

### E. Inject WRITING_DISCIPLINE into all guide prompts

Add the `WRITING_DISCIPLINE` block to:
- GETTING_STARTED_PROMPT
- BEGINNER_SECTION_PROMPT
- BEGINNER_PARENT_PROMPT
- BUILD_ANALYSIS_PROMPT
- ALGORITHM_DEEPDIVE_PROMPT
- ALGORITHM_PARENT_PROMPT
- REPO_OVERVIEW_PROMPT
- MODULE_OVERVIEW_PROMPT

### F. Fix the "all docs have the same skeleton" problem for overviews

Current REPO_OVERVIEW_PROMPT and MODULE_OVERVIEW_PROMPT prescribe a rigid numbered structure (1. What / 2. Architecture / 3. Decisions / 4. Module guide / 5. Workflows). This guarantees every overview shares the same skeleton.

Change to: provide the topics as a **mandatory coverage checklist**, but explicitly say "all checklist items are required — but organize them in whatever order makes the narrative flow best for THIS specific project/module. Do NOT mechanically follow the checklist order in your output. Weave topics together where they naturally connect."

This preserves coverage guarantee (all topics must appear) while allowing structural variety per project.

### G. Naming prompt bilingual format

`naming.py:61` currently instructs: `"title": Use format: 中文名 (English Name)`. This is fine for the tree, but should explicitly state that the Chinese name is the primary display name and the English name is for identifier stability.

---

## Implementation Order

1. Create `_WRITING_DISCIPLINE` constant
2. Create `_SHARED_*` blocks, refactor SYSTEM_PROMPT / LEAF_SYSTEM_PROMPT by composition
3. Update `_build_language_section()` with richer language instructions
4. Inject `_WRITING_DISCIPLINE` into all guide/overview prompts
5. Fix overview prompt skeleton rigidity
6. Update WRITING_APPROACH examples for structural variety
7. Run existing tests to verify no regressions
8. Test with a small repo to compare output quality

## Non-Goals

- No scripted de-AI detection (user said: "不用脚本检测")
- No changes to clustering prompts (CLUSTER_REPO_PROMPT, CLUSTER_MODULE_PROMPT) — they output JSON, not prose
- No changes to repair prompts (MATH_REPAIR_USER, _REPAIR_USER) — they output code, not prose
