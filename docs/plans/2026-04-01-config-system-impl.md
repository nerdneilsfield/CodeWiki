# Config System Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace CodeWiki’s current env/JSON/keyring configuration stack with one TOML-based multi-provider config system shared by CLI, backend, and web/background worker entrypoints.

**Architecture:** Introduce a typed TOML loader that produces a provider-aware runtime config, make model references use `provider_name/model_name`, and refactor `llm_services.py` to resolve concrete provider clients per model. CLI `generate`, backend entrypoints, and background worker all load the same config path; legacy `config.json` becomes compatibility-only instead of the main path.

**Tech Stack:** Python 3.12, `tomllib`, `tomli_w`, typed dataclasses/models already in repo, pytest, Click, existing OpenAI SDK, provider-specific SDKs if already present

---

### Task 1: Add TOML fixtures, writer dependency, and loader tests

**Files:**
- Modify: `pyproject.toml`
- Create: `config.example.toml`
- Create: `tests/test_config_loader.py`

**Step 1: Write failing loader tests**

Add tests covering:
- valid TOML with two providers
- `provider/model` resolution
- env-based API key references
- missing provider name in model ref
- invalid model ref format
- fallback model referencing an undefined provider

**Step 2: Add TOML writer dependency**

Add `tomli_w` to dependencies for `config init` output.

**Step 3: Run tests to verify they fail**

Run: `pytest tests/test_config_loader.py -v`
Expected: FAIL because loader module does not exist yet

**Step 4: Add `config.example.toml`**

Create a repo-root `config.example.toml` showing:
- `[runtime]`
- `[tokens]`
- `[generation]`
- `[agent]`
- multiple `[[providers]]`

Keep examples close to the final supported schema and reference only providers that are actually defined in the file.

**Step 5: Commit**

```bash
git add pyproject.toml config.example.toml tests/test_config_loader.py
git commit -m "test(config): add TOML schema fixtures and writer dependency"
```

---

### Task 2: Add typed config models, overrides, and update fixtures

**Files:**
- Create: `codewiki/src/config_loader.py`
- Modify: `codewiki/src/config.py`
- Modify: test fixtures that construct `Config` directly
- Test: `tests/test_config_loader.py`
- Test: any directly affected config fixture tests

**Step 1: Write the minimal failing tests for model loading**

Add tests for:
- `load_app_config(path)` returns typed config
- `resolve_model_ref("openai/gpt-4o-mini")`
- `fallback_models` keep order across providers
- `to_runtime_config(..., overrides=...)` applies the documented merge order

**Step 2: Run the targeted failing tests**

Run: `pytest tests/test_config_loader.py -k "load_app_config or resolve_model_ref or overrides" -v`
Expected: FAIL on missing functions/classes

**Step 3: Implement loader and models**

Implement:
- `ProviderConfig`
- `RuntimeSection`
- `TokensSection`
- `GenerationSection`
- `AgentSection`
- `RuntimeOverrides`
- `AppConfig`
- `ResolvedModel`
- `load_app_config(path)`
- `resolve_model_ref(model_ref)`

In `codewiki/src/config.py`, either:
- replace old `Config` with a thinner runtime config, or
- keep a compatibility wrapper while moving source-of-truth loading into `config_loader.py`

Do not keep `llm_base_url` / `llm_api_key` as the only runtime LLM fields.

**Step 4: Update test fixtures immediately**

In this same task, update tests that construct `Config(llm_base_url=..., llm_api_key=...)` or mutate those fields directly, so later tasks are not blocked by stale fixtures.

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config_loader.py tests/test_guide_generator.py tests/test_perf_llm_client.py tests/test_perf_async_client_pool.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add codewiki/src/config_loader.py codewiki/src/config.py tests/
git commit -m "feat(config): add typed TOML loader, overrides, and updated fixtures"
```

---

### Task 3: Refactor LLM services to resolve provider/model per stage

**Files:**
- Modify: `codewiki/src/be/llm_services.py`
- Create: `tests/test_llm_services_provider_resolution.py`

**Step 1: Write failing tests for provider-aware model creation**

Cover:
- OpenAI-compatible provider resolution
- fallback chain across multiple providers
- long-context model from different provider
- unsupported provider type fails fast
- Claude native provider path construction

**Step 2: Run failing tests**

Run: `pytest tests/test_llm_services_provider_resolution.py -v`
Expected: FAIL because `llm_services.py` still expects one provider in config

**Step 3: Implement provider-aware factories**

Refactor `llm_services.py` so it works from resolved model refs instead of global `config.llm_base_url`.

Add helpers like:
- `get_provider_client(provider_config)`
- `create_model_from_ref(runtime_config, "provider/model")`
- `create_fallback_models(runtime_config)` using ordered cross-provider refs

Implementation choices:
- `openai_compatible` and `azure_openai` stay on the existing OpenAI-based path
- `claude` uses a native Anthropic-backed path
- unsupported provider types raise a clear configuration/runtime error

Keep existing retry/backoff logic, but remove single-provider assumptions.

**Step 4: Run the new tests**

Run: `pytest tests/test_llm_services_provider_resolution.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/llm_services.py tests/test_llm_services_provider_resolution.py
git commit -m "feat(llm): resolve provider-aware model refs"
```

---

### Task 4: Switch CLI generate to `--config` with explicit fallback order

**Files:**
- Modify: `codewiki/cli/commands/generate.py`
- Modify: `codewiki/cli/models/config.py`
- Modify: `codewiki/cli/config_manager.py`
- Test: `tests/test_cli_generate_config_file.py`
- Test: `tests/test_cli_generate_legacy_config_compat.py`

**Step 1: Write failing CLI tests**

Cover:
- `codewiki generate --config path.toml`
- runtime overrides layered on top of TOML
- `--config` missing but legacy `~/.codewiki/config.json` exists → compatibility shim + warning
- `--config` missing and no legacy config exists → helpful error pointing to `codewiki config init`
- no dependency on `ConfigManager` for the main TOML generation path

**Step 2: Run failing CLI tests**

Run: `pytest tests/test_cli_generate_config_file.py tests/test_cli_generate_legacy_config_compat.py -v`
Expected: FAIL because generate still depends on `ConfigManager`

**Step 3: Implement the new CLI path**

Change `generate` to use this fallback order:
1. explicit `--config`
2. legacy config shim from `~/.codewiki/config.json`
3. hard error with `config init` guidance

Preserve existing include/exclude/focus/doc-type/instructions overrides by mapping them into `RuntimeOverrides`.

`codewiki/cli/models/config.py` and `config_manager.py` should be kept only as compatibility layers if still needed by old commands/tests.

**Step 4: Run CLI tests**

Run: `pytest tests/test_cli_generate_config_file.py tests/test_cli_generate_legacy_config_compat.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/cli/commands/generate.py codewiki/cli/models/config.py codewiki/cli/config_manager.py tests/test_cli_generate_config_file.py tests/test_cli_generate_legacy_config_compat.py
git commit -m "feat(cli): load generation config from TOML with legacy fallback"
```

---

### Task 5: Unify backend main entrypoint, web app, background worker, and web config boundary

**Files:**
- Modify: `codewiki/src/be/main.py`
- Modify: `codewiki/src/fe/background_worker.py`
- Modify: `codewiki/src/fe/web_app.py`
- Modify: `codewiki/src/fe/config.py`
- Test: `tests/test_background_worker_config_loading.py`
- Test: `tests/test_be_main_config_loading.py`
- Test: `tests/test_web_app_config_loading.py`

**Step 1: Write failing entrypoint tests**

Cover:
- backend main accepts config path
- background worker uses the same loader instead of env defaults
- web app passes config path through to job processing
- `WebAppConfig` still owns queue/temp/cache settings but not LLM/provider settings

**Step 2: Run failing tests**

Run: `pytest tests/test_background_worker_config_loading.py tests/test_be_main_config_loading.py tests/test_web_app_config_loading.py -v`
Expected: FAIL because current paths still call `Config.from_args()` or assume env defaults

**Step 3: Implement unified loading**

Refactor:
- `be/main.py` to accept `--config`
- background worker to construct runtime config from the same TOML
- web app to carry config path into background jobs
- `fe/config.py` to stay focused on web infra settings only

Do not leave silent env-default behavior on these paths.

**Step 4: Run tests**

Run: `pytest tests/test_background_worker_config_loading.py tests/test_be_main_config_loading.py tests/test_web_app_config_loading.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/src/be/main.py codewiki/src/fe/background_worker.py codewiki/src/fe/web_app.py codewiki/src/fe/config.py tests/test_background_worker_config_loading.py tests/test_be_main_config_loading.py tests/test_web_app_config_loading.py
git commit -m "feat(runtime): unify config loading across entrypoints"
```

---

### Task 6: Deprecate legacy config commands and make `config init` mandatory

**Files:**
- Modify: `codewiki/cli/commands/config.py`
- Test: `tests/test_cli_config_commands.py`

**Step 1: Write failing tests for new CLI config workflow**

Cover:
- `config set` warns it is legacy/deprecated
- `config show --config path.toml` reads TOML
- `config init` writes a template TOML
- `config validate --config path.toml` checks schema

**Step 2: Run failing tests**

Run: `pytest tests/test_cli_config_commands.py -v`
Expected: FAIL for new subcommands/behavior

**Step 3: Implement CLI cleanup**

Shift the config command group from persistent state management toward file-oriented helpers.

Required in this phase:
- make legacy status explicit
- add `config init`
- add `config validate`
- stop documenting `config set` as the preferred workflow

**Step 4: Run tests**

Run: `pytest tests/test_cli_config_commands.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add codewiki/cli/commands/config.py tests/test_cli_config_commands.py
git commit -m "refactor(cli): make TOML config workflow primary"
```

---

### Task 7: Update docs and remove remaining single-provider assumptions

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`
- Modify: any remaining tests that still hard-code `llm_base_url` / `llm_api_key` assumptions

**Step 1: Update documentation**

Document:
- `config.example.toml`
- `codewiki generate --config config.toml`
- `provider/model` syntax
- env key references
- legacy fallback behavior during migration
- multi-provider examples

**Step 2: Sweep remaining stale tests**

Remove or adapt remaining single-provider assumptions that were not already handled in Task 2.

**Step 3: Run targeted regression**

Run:
```bash
pytest tests/test_config_loader.py        tests/test_llm_services_provider_resolution.py        tests/test_cli_generate_config_file.py        tests/test_cli_generate_legacy_config_compat.py        tests/test_background_worker_config_loading.py        tests/test_be_main_config_loading.py        tests/test_web_app_config_loading.py        tests/test_cli_config_commands.py -v
```

Expected: PASS

**Step 4: Commit**

```bash
git add README.md README_ZH.md tests/
git commit -m "docs(config): document TOML-based multi-provider workflow"
```

---

### Task 8: Full regression and cleanup

**Files:**
- Modify: any touched files from previous tasks as needed

**Step 1: Run full relevant regression**

Run:
```bash
pytest tests/test_config_loader.py        tests/test_llm_services_provider_resolution.py        tests/test_cli_generate_config_file.py        tests/test_cli_generate_legacy_config_compat.py        tests/test_background_worker_config_loading.py        tests/test_be_main_config_loading.py        tests/test_web_app_config_loading.py        tests/test_clustering_pipeline.py        tests/test_generation_glossary.py        tests/test_postprocess_link_validator.py -v
```

Expected: PASS

**Step 2: Manual smoke test**

Run a local generate command against a small repo:

```bash
codewiki generate --config config.example.toml --output /tmp/codewiki-docs
```

Expected:
- config loads successfully
- main/cluster/fallback models resolve
- docs are generated without using `ConfigManager` on the primary path

**Step 3: Final commit**

```bash
git add .
git commit -m "feat(config): unify CodeWiki on TOML multi-provider configuration"
```
