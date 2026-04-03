"""Tests for the TOML-based multi-provider config loader."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _ensure_namespace_packages():
    root = Path(__file__).resolve().parents[1]
    package_paths = {
        "codewiki": root / "codewiki",
        "codewiki.src": root / "codewiki" / "src",
    }
    for name, path in package_paths.items():
        module = sys.modules.get(name)
        if module is None or not hasattr(module, "__path__"):
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module


def _load_loader_module():
    _ensure_namespace_packages()
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
    return module.load_config, module.RuntimeOverrides, module.resolve_model_ref


def test_config_example_toml_has_defined_providers(monkeypatch):
    load_config, _, _ = _load_loader_api()
    config_path = Path("config.example.toml")
    assert config_path.exists()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")

    cfg = load_config(config_path, repo_path="/tmp/fake-repo")

    assert cfg.main_model == "openai/gpt-4o-mini"
    assert cfg.cluster_model == "claude/claude-sonnet-4-5-20250929"
    assert [provider.name for provider in cfg.providers] == ["openai", "claude"]
    assert cfg.context == "cli"


def test_resolve_model_ref_accepts_provider_model_format():
    _, _, resolve_model_ref = _load_loader_api()
    resolved = resolve_model_ref("openai/gpt-4o-mini")

    assert resolved.provider_name == "openai"
    assert resolved.model_name == "gpt-4o-mini"


def test_resolve_model_ref_rejects_unknown_provider():
    _, _, resolve_model_ref = _load_loader_api()
    with pytest.raises(ValueError, match="provider"):
        resolve_model_ref("missing/gpt-4o-mini")


def test_resolve_model_ref_rejects_invalid_format():
    _, _, resolve_model_ref = _load_loader_api()
    with pytest.raises(ValueError, match="provider/model"):
        resolve_model_ref("gpt-4o-mini")


def test_load_config_rejects_missing_env_api_key(tmp_path, monkeypatch):
    load_config, _, _ = _load_loader_api()
    monkeypatch.delenv("MISSING_OPENAI_API_KEY", raising=False)

    config_path = tmp_path / "missing-env.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n"
        "[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n"
        "[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=['env:MISSING_OPENAI_API_KEY']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="MISSING_OPENAI_API_KEY"):
        load_config(config_path, repo_path="/tmp/fake-repo")


def test_load_config_applies_overrides_and_preserves_provider_registry(monkeypatch):
    module = _load_loader_module()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")

    cfg = module.load_config(
        Path("config.example.toml"),
        repo_path="/tmp/fake-repo",
        overrides=module.RuntimeOverrides(
            output_dir="/tmp/generated-docs",
            main_model="claude/claude-sonnet-4-5-20250929",
            max_concurrent=7,
            output_language="zh",
            agent_instructions={"doc_type": "api"},
        ),
    )

    assert cfg.main_model == "claude/claude-sonnet-4-5-20250929"
    assert cfg.cluster_model == "claude/claude-sonnet-4-5-20250929"
    assert cfg.max_concurrent == 7
    assert cfg.output_language == "zh"
    assert cfg.docs_dir == "/tmp/generated-docs"
    assert cfg.output_dir == "/tmp/generated-docs/temp"
    assert cfg.providers is not None
    assert [provider.name for provider in cfg.providers] == ["openai", "claude"]
    assert cfg.agent_instructions == {"doc_type": "api"}
