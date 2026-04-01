# Config System Redesign Design

**Date:** 2026-04-01
**Scope:** Replace the current single-provider config stack with one TOML-based multi-provider config system shared by CLI, backend, and web/background worker entrypoints.
**Primary Goal:** Make `provider_name/model_name` the stable model reference format so different stages can use different providers in one run.

## Core Problem

CodeWiki currently has three overlapping configuration paths:

1. `codewiki/src/config.py` runtime config built mostly from environment variables
2. `codewiki/cli/config_manager.py` persistent `~/.codewiki/config.json + keyring`
3. ad-hoc web/background worker setup that calls `Config.from_args()` and inherits env defaults

This causes four practical issues:

- Runtime only understands a single provider (`llm_base_url + llm_api_key`)
- CLI settings and runtime settings are different data models
- Web/background worker do not share the same config source as CLI
- Model selection cannot express mixed providers such as `openai/gpt-4o-mini` for generation and `claude/claude-sonnet-4-5-20250929` for clustering

## Design Goals

1. **One config source of truth**: one TOML file drives CLI, backend, web app, and background worker
2. **Multi-provider first**: multiple providers can coexist in one config file
3. **Stable model reference format**: all model fields use `provider_name/model_name`
4. **Minimal behavioral churn**: keep existing generation pipeline structure where possible; change config loading and model/provider resolution instead
5. **Reasonable migration**: old `config.json` path becomes legacy, not the primary path

## Non-Goals

- No quota scheduler or per-key backoff orchestration in this phase
- No dynamic provider capability negotiation beyond what CodeWiki already needs
- No interactive “config wizard” as the main workflow
- No attempt to preserve `config set` as the primary path

## Proposed Config Shape

The new config file will be TOML-based and checked by a typed loader.

```toml
[runtime]
output_dir = "docs"
max_depth = 2
max_concurrent = 3
max_retries = 2
output_language = "en"
postprocess_strict = false

[tokens]
max_tokens = 32768
max_token_per_module = 36369
max_token_per_leaf_module = 16000
long_context_threshold = 200000

[generation]
main_model = "openai/gpt-4o-mini"
cluster_model = "claude/claude-sonnet-4-5-20250929"
fallback_models = [
  "openai/gpt-4o-mini",
  "claude/claude-sonnet-4-5-20250929",
]
long_context_model = "openai/gpt-4.1"

[agent]
doc_type = "architecture"
focus_modules = ["src/core", "src/api"]
custom_instructions = "Prefer diagrams for component relationships."

[[providers]]
name = "openai"
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_keys = ["env:OPENAI_API_KEY"]
model_list = ["gpt-4o-mini", "gpt-4.1"]
extra_headers = {}

[[providers]]
name = "claude"
type = "claude"
api_keys = ["env:ANTHROPIC_API_KEY"]
anthropic_version = "2024-02-15"
model_list = ["claude-sonnet-4-5-20250929"]
extra_headers = {}
```

## Data Model

### `ProviderConfig`

Represents one configured provider entry.

Common fields:

- `name: str`
- `type: Literal[...]`
- `api_keys: list[str | dict]`
- `model_list: list[str]`
- `extra_headers: dict[str, str]`

Provider-specific optional fields:

- `base_url`
- `endpoint`
- `api_version`
- `deployment`
- `anthropic_version`
- `project_id`
- `location`
- `credentials_path`

### `AppConfig`

Top-level loaded config object.

Fields:

- `runtime`
- `tokens`
- `generation`
- `agent`
- `providers`

Key helpers:

- `get_provider(name) -> ProviderConfig`
- `resolve_model_ref("openai/gpt-4o-mini") -> ResolvedModel`
- `to_runtime_config(repo_path, overrides: RuntimeOverrides | None = None) -> RuntimeConfig`

### `RuntimeOverrides`

A thin per-run override layer used by CLI/web/job orchestration.

Fields should include only runtime knobs that users already override today:

- `output_dir`
- `max_depth`
- `max_tokens`
- `max_token_per_module`
- `max_token_per_leaf_module`
- `max_concurrent`
- `max_retries`
- `output_language`
- `main_model`
- `cluster_model`
- `fallback_models`
- `long_context_model`
- `long_context_threshold`
- `agent_instructions`

Merge order:

1. TOML file defaults
2. compatibility shim values from legacy `config.json` when TOML is absent
3. explicit runtime overrides from CLI/web/job request

This keeps one place responsible for override semantics and avoids duplicating the old `Config.from_cli()` merge behavior in multiple entrypoints.

### `RuntimeConfig`

This replaces the current `codewiki.src.config.Config` as the runtime object passed into backend generation code.

It should contain:

- repo/output paths
- token/concurrency knobs
- agent instructions
- resolved model references for main / cluster / long-context / fallback
- provider registry

The runtime object should no longer store only `llm_base_url` and `llm_api_key`.

## Model Reference Resolution

All model references use the format:

```text
provider_name/model_name
```

Examples:

- `openai/gpt-4o-mini`
- `claude/claude-sonnet-4-5-20250929`
- `openai/gpt-4.1`

Resolution rules:

1. Split once on `/`
2. Find matching provider by `name`
3. Validate model name exists in `provider.model_list` when the list is non-empty
4. Return a resolved object containing:
   - provider config
   - provider type
   - raw model name
   - chosen credential source

Fallback chains remain ordered, but now each fallback may come from a different provider.

## Provider Architecture

`llm_services.py` becomes provider-aware. The important shift is:

- today: `Config -> one OpenAI-compatible client/provider`
- new: `ResolvedModel -> provider factory -> concrete client/model object`

### Required provider implementations in this phase

1. `openai_compatible`
2. `azure_openai`
3. `claude`

### Claude implementation path

This must be explicit because the current stack is built around `OpenAIModel`.

Decision for this phase:

- `claude` uses a **native Anthropic provider path**, not an OpenAI-compatible shim
- the provider factory should build an Anthropic-backed model/client path for `type = "claude"`
- if the required Anthropic/pydantic-ai provider dependency is missing, config loading or provider construction must fail fast with a clear error

This avoids silently tying Claude support to a vendor-specific compatibility endpoint that may not exist for all users.

### Cache behavior

The existing `lru_cache(base_url, api_key)` pattern remains valid for the OpenAI-compatible family because different provider endpoints naturally produce different cache keys.

Native-provider paths should use analogous cache keys based on the provider-specific connection parameters. This is an extension, not a total cache rewrite.

## Entry Point Unification

### CLI generate

Primary path becomes:

```bash
codewiki generate --config /path/to/config.toml
```

If `--config` is omitted:

1. check legacy `~/.codewiki/config.json`
2. if present, load it through a compatibility shim and emit a deprecation warning
3. if absent, fail with a clear message that points users to `codewiki config init`

`generate` may still allow per-run overrides for runtime knobs, but model/provider selection should come from config by default.

### Web app / background worker

Web/background worker must also receive a config path (or preloaded `AppConfig`) and use the same loader.

No more “web path silently falls back to env defaults” behavior.

### WebAppConfig boundary

`codewiki/src/fe/config.py` should remain responsible only for web infrastructure settings such as temp/cache directories, queue size, and server defaults.

LLM/provider/model settings must move out of `WebAppConfig` and into the shared TOML-driven config path.

### Legacy config command

`codewiki config set/show` stops being the main path.

Recommended treatment for this phase:

- keep the command group for compatibility
- mark it legacy/deprecated
- repurpose it toward:
  - `config init` (write template TOML)
  - `config validate`
  - `config show --config path`

Do not keep `config.json + keyring` as the primary storage model.

## Secret Handling

This phase will use string references like:

- `env:OPENAI_API_KEY`
- `env:ANTHROPIC_API_KEY`

The loader resolves these at runtime.

This keeps the TOML portable and avoids rebuilding a new secret-storage abstraction in the same refactor.

Because `tomllib` is read-only, template writing for `config init` should use a TOML writer dependency such as `tomli_w`.

## Migration Strategy

### Phase 1 migration stance

- New TOML path is primary and documented
- Existing `config.json` is legacy-only
- Internal runtime no longer depends on `ConfigManager`
- A compatibility shim remains temporarily for old callers and existing users

### Documentation changes

Update:

- `README.md`
- `README_ZH.md`
- CLI help text

Examples should use `--config config.toml` instead of “set persistent config first”.

## Main Trade-offs

### Why TOML as the single source of truth?

Because CodeWiki now has enough knobs and nested provider config that env vars and ad-hoc JSON are too flat and too fragmented.

### Why `provider/model` instead of separate `provider` + `model` fields everywhere?

Because most call sites reason about “which model should this stage use?” as one concept. The combined string is easier to pass around and makes mixed-provider fallback chains explicit.

### Why env references for API keys instead of keyring?

Because this refactor is about unifying config and provider routing. Keyring adds another storage system and another migration problem. Environment references are simple, explicit, and already common in provider tooling.

## Success Criteria

The redesign is complete when:

1. One TOML file can configure multiple providers
2. `main_model`, `cluster_model`, `fallback_models`, and `long_context_model` all accept `provider/model`
3. CLI generate, backend, web app, and background worker all load the same config shape
4. Generation can use different providers in different stages without code changes outside config
5. Old `config.json + keyring` is no longer the documented primary path

## Known Issues / Post-Implementation Findings

### F1 (Medium) — `config validate` passes with unresolvable `env:` references

**Observed:** `codewiki config validate --config config.toml` exits 0 even when `api_keys`
contain `env:VAR_NAME` entries whose env vars are not set.

**Root cause:** `_validate_toml()` calls `load_app_config(path, resolve_secrets=False)` so
that operators can run `validate` and `show` on machines that do not hold the secrets (CI,
review, documentation). Structural validation and model-ref resolution still run; only secret
resolution is skipped.

**Impact:** A typo such as `env:OPNEAI_API_KEY` will pass `validate` and only fail at
`generate` time.

**Proposed remedy:** Add a `--check-secrets` flag (or `--strict-secrets`) to `config validate`
that re-runs loading with `resolve_secrets=True`.  Default stays lenient so the command
remains usable without secrets present.

---

### F2 (Low) — `BackgroundWorker` still uses `print()` for progress noise

**Observed:** Tests that exercise `BackgroundWorker._process_job` and
`BackgroundWorker.load_job_statuses` emit lines such as
`Loaded 1 completed jobs from disk` and `Job test-job: Documentation generated successfully`
to stdout.

**Root cause:** `codewiki/src/fe/background_worker.py` still uses `print()` calls for
operational messages instead of the module-level `logger`.

**Impact:** Test output is noisy today; will worsen as more worker/web paths are tested.

**Proposed remedy:** Replace all `print(...)` in `background_worker.py` with
`logger.info(...)` / `logger.warning(...)`. Tests that need to assert on log output can use
`pytest`'s `caplog` fixture.
