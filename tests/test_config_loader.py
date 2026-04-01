"""Tests for the TOML-based multi-provider config loader."""

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_loader_module():
    module_path = Path("codewiki/src/config_loader.py")
    spec = importlib.util.spec_from_file_location("codewiki.src.config_loader", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("codewiki.src.config_loader", module)
    spec.loader.exec_module(module)
    return module


def _load_loader_api():
    module = _load_loader_module()
    return module.load_app_config, module.resolve_model_ref


def test_config_example_toml_has_defined_providers(monkeypatch):
    load_app_config, _ = _load_loader_api()
    config_path = Path("config.example.toml")
    assert config_path.exists()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")

    app_config = load_app_config(config_path)

    assert app_config.generation.main_model == "openai/gpt-4o-mini"
    assert app_config.generation.cluster_model == "claude/claude-sonnet-4-5-20250929"
    assert [provider.name for provider in app_config.providers] == ["openai", "claude"]


def test_resolve_model_ref_accepts_provider_model_format():
    _, resolve_model_ref = _load_loader_api()
    resolved = resolve_model_ref("openai/gpt-4o-mini")

    assert resolved.provider_name == "openai"
    assert resolved.model_name == "gpt-4o-mini"


def test_resolve_model_ref_rejects_unknown_provider():
    _, resolve_model_ref = _load_loader_api()
    with pytest.raises(ValueError, match="provider"):
        resolve_model_ref("missing/gpt-4o-mini")


def test_resolve_model_ref_rejects_invalid_format():
    _, resolve_model_ref = _load_loader_api()
    with pytest.raises(ValueError, match="provider/model"):
        resolve_model_ref("gpt-4o-mini")


def test_load_app_config_rejects_missing_env_api_key(tmp_path, monkeypatch):
    load_app_config, _ = _load_loader_api()
    monkeypatch.delenv("MISSING_OPENAI_API_KEY", raising=False)

    config_path = tmp_path / "missing-env.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n"
        "[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n"
        "[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=['env:MISSING_OPENAI_API_KEY']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="MISSING_OPENAI_API_KEY"):
        load_app_config(config_path)


def test_to_runtime_config_applies_overrides_and_preserves_provider_registry(monkeypatch):
    module = _load_loader_module()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")
    app_config = module.load_app_config(Path("config.example.toml"))

    runtime_config = app_config.to_runtime_config(
        repo_path="/tmp/fake-repo",
        overrides=module.RuntimeOverrides(
            output_dir="/tmp/generated-docs",
            main_model="claude/claude-sonnet-4-5-20250929",
            max_concurrent=7,
            output_language="zh",
            agent_instructions={"doc_type": "api"},
        ),
    )

    assert runtime_config.main_model == "claude/claude-sonnet-4-5-20250929"
    assert runtime_config.cluster_model == "claude/claude-sonnet-4-5-20250929"
    assert runtime_config.max_concurrent == 7
    assert runtime_config.output_language == "zh"
    assert runtime_config.docs_dir == "/tmp/generated-docs"
    assert runtime_config.providers is not None
    assert [provider.name for provider in runtime_config.providers] == ["openai", "claude"]
    assert runtime_config.agent_instructions == {"doc_type": "api"}
