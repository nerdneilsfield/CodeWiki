# Ruff + Ty Design

**Date:** 2026-04-03

**Goal:** Replace `mypy` with `ty`, expand Ruff into the repository's primary lint/format tool, and add local `pre-commit` enforcement without introducing CI changes.

**Scope:**
- Add a practical Ruff configuration for this repository.
- Add `ty` configuration and dependency.
- Remove `mypy` from active development tooling.
- Add `.pre-commit-config.yaml` with Ruff and `ty`.
- Update docs and developer instructions.

**Out of scope:**
- GitHub Actions / CI integration
- Making `ty` cover every fixture and every test helper on day one
- Large-scale style churn unrelated to tool adoption

---

## Current State

The repository already has:

- `ruff` in `project.optional-dependencies.dev`
- a minimal `[tool.ruff]` table in `pyproject.toml`
- `mypy` in `project.optional-dependencies.dev`
- no `ty`
- no `.pre-commit-config.yaml`

That means the repo already has the beginnings of a Python toolchain, but not a coherent one. The type-checker decision is still split, and local enforcement is manual.

---

## Tool Roles

### Ruff

Ruff will own:

- linting
- formatting
- import cleanup and other safe autofixes

Ruff should become the first tool developers run locally, and the first hook in pre-commit.

### Ty

`ty` will replace `mypy` as the repository's type checker.

It should initially focus on the real code paths:

- `codewiki/`
- `codewiki/cli/`
- `codewiki/src/`

Tests should not block adoption. They can be relaxed via `tool.ty.overrides` or partial include/exclude rules.

### Pre-commit

Pre-commit will provide local enforcement only:

- `ruff check --fix`
- `ruff format`
- `ty check`

No CI work is included in this design.

---

## Recommended Configuration Strategy

### Ruff

Use `pyproject.toml` as the single configuration source.

Recommended approach:

- keep `line-length = 100` to match the current codebase
- keep `target-version` aligned with the runtime target already in the repo
- use conservative lint selection first
- add targeted `per-file-ignores` instead of broad global ignores

Initial Ruff policy should optimize for adoption:

- enable correctness and cleanup rules first
- avoid a giant one-shot style rewrite unless Ruff itself can autofix it safely
- let formatting be consistent, but avoid enabling unstable preview behavior

### Ty

Also configure through `pyproject.toml`.

Recommended initial posture:

- `tool.ty.environment.python-version` aligned with the repo's supported Python version
- `tool.ty.src.include` focused on first-party code
- `tool.ty.src.exclude` used for generated/fixture/noisy paths if needed
- `tool.ty.overrides` used to relax checks under `tests/**`

This gives the repo a single type-checker with a realistic rollout path.

### Mypy removal

`mypy` should be removed from:

- `project.optional-dependencies.dev`
- `tool.mypy` config
- README / developer docs
- any helper scripts or instructions that still call it

This avoids maintaining two overlapping type-checkers.

---

## Repository Integration

### Configuration source

All tool configuration should live in `pyproject.toml`.

This keeps:

- dependency metadata
- pytest config
- Ruff config
- `ty` config

in one place.

### Pre-commit

Add `.pre-commit-config.yaml` and keep it minimal.

Recommended hook order:

1. Ruff lint with autofix
2. Ruff format
3. `ty` check

That order reduces noise by letting easy syntax/style cleanups happen before type checking.

### Documentation

Update README and any development docs to reflect the new canonical local workflow:

```bash
ruff check .
ruff format --check .
ty check
pre-commit run --all-files
```

---

## Risk Areas

### 1. Ty rollout noise

`ty` may immediately surface unresolved imports or typing gaps in test helpers and fixture-heavy code.

Mitigation:

- scope initial coverage to first-party source paths
- use `tool.ty.overrides` for `tests/**`
- avoid making test noise block initial adoption

### 2. Ruff churn

If we enable too many rule families at once, the repo may get a noisy first pass.

Mitigation:

- start with a conservative rule set
- prefer autofixable correctness and cleanup rules
- keep repo-specific ignores narrow and explicit

### 3. Developer friction

Adding pre-commit without documenting install/use commands can frustrate contributors.

Mitigation:

- document `uv sync --extra dev`
- document `pre-commit install`
- document `pre-commit run --all-files`

---

## Recommended End State

After this work:

- Ruff is the repo's lint/format tool
- `ty` is the repo's only static type checker
- `mypy` is gone
- local enforcement exists via pre-commit
- CI remains unchanged

This is the cleanest end state for the current repository size and maturity.

