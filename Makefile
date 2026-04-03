.DEFAULT_GOAL := help

.PHONY: help install lint format format-check typecheck test test-v7 hooks check quality

help:
	@printf "Available targets:\n"
	@printf "  install        Install the package in editable mode\n"
	@printf "  lint           Run Ruff lint checks\n"
	@printf "  format         Format the repository with Ruff\n"
	@printf "  format-check   Check formatting without modifying files\n"
	@printf "  typecheck      Run ty type checking\n"
	@printf "  test           Run the full test suite\n"
	@printf "  test-v7        Run the focused v7 regression suite\n"
	@printf "  hooks          Run all pre-commit hooks\n"
	@printf "  check          Run lint, format-check, and typecheck\n"
	@printf "  quality        Run check plus tests\n"

install:
	uv pip install -e .

lint:
	uv run ruff check . --output-format concise

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run ty check

test:
	uv run python -m pytest tests/ --cov=codewiki --cov-report=term-missing -q

test-v7:
	uv run python -m pytest -q tests/test_documentation_tree_utils.py tests/test_documentation_overview.py tests/test_documentation_scheduler.py tests/test_generation_state.py tests/test_str_replace_editor_assigned_filename.py tests/test_module_doc_filename.py tests/test_link_rewriter.py tests/test_static_generator_corner_cases.py tests/test_overview_language.py tests/test_generation_glossary.py tests/test_documentation_generator_state_bridge.py tests/test_agent_assigned_filename.py tests/test_postprocess_link_validator.py tests/test_perf_docs_fixer.py tests/test_documentation_generator_worker_cleanup.py tests/test_guide_generator.py -k 'not network'

hooks:
	uv run pre-commit run --all-files

check: lint format-check typecheck

quality: check test
