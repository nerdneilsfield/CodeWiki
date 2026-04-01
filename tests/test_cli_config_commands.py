"""
Tests for the config subcommands:
  - config init  (create TOML template)
  - config show  (TOML path and legacy fallback)
  - config validate (TOML path and legacy fallback)
  - config set   (deprecated — shows warning)
  - config agent (deprecated — shows warning)
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from codewiki.cli.commands.config import config_group, _LEGACY_WARNING, _TOML_TEMPLATE


# ── helpers ───────────────────────────────────────────────────────────────────

MINIMAL_TOML = (
    "[runtime]\n"
    "output_dir = 'docs'\n"
    "[generation]\n"
    "main_model = 'openai/gpt-4o-mini'\n"
    "cluster_model = 'openai/gpt-4o-mini'\n"
    "[[providers]]\n"
    "name = 'openai'\n"
    "type = 'openai_compatible'\n"
    "model_list = ['gpt-4o-mini']\n"
    "api_keys = []\n"
)


def _runner():
    return CliRunner()


def _write_toml(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(MINIMAL_TOML, encoding="utf-8")
    return p


# ── config init ───────────────────────────────────────────────────────────────

def test_config_init_creates_file(tmp_path):
    dest = tmp_path / "codewiki.toml"
    result = _runner().invoke(config_group, ["init", "--output", str(dest)])

    assert result.exit_code == 0
    assert dest.exists()
    content = dest.read_text()
    # Template must contain essential section headers
    assert "[runtime]" in content
    assert "[generation]" in content
    assert "[[providers]]" in content


def test_config_init_default_output_name():
    """init with no --output writes to config.toml in cwd."""
    with CliRunner().isolated_filesystem():
        result = CliRunner().invoke(config_group, ["init"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "config.toml" in result.output


def test_config_init_refuses_to_overwrite_without_force(tmp_path):
    dest = tmp_path / "codewiki.toml"
    dest.write_text("existing content")

    result = _runner().invoke(config_group, ["init", "--output", str(dest)])

    assert result.exit_code != 0
    assert dest.read_text() == "existing content"


def test_config_init_force_overwrites_existing_file(tmp_path):
    dest = tmp_path / "codewiki.toml"
    dest.write_text("old content")

    result = _runner().invoke(config_group, ["init", "--output", str(dest), "--force"])

    assert result.exit_code == 0
    content = dest.read_text()
    assert "old content" not in content
    assert "[runtime]" in content


def test_config_init_shows_next_steps(tmp_path):
    dest = tmp_path / "config.toml"
    result = _runner().invoke(config_group, ["init", "--output", str(dest)])

    assert result.exit_code == 0
    assert "Next steps" in result.output
    assert "codewiki generate --config" in result.output


# ── config validate ───────────────────────────────────────────────────────────

def test_config_validate_toml_succeeds_without_env_secrets(tmp_path):
    """validate must not require env: secrets to be present."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[runtime]\noutput_dir = 'docs'\n"
        "[generation]\nmain_model = 'openai/gpt-4o-mini'\ncluster_model = 'openai/gpt-4o-mini'\n"
        "[[providers]]\nname = 'openai'\ntype = 'openai_compatible'\n"
        "model_list = ['gpt-4o-mini']\napi_keys = ['env:OPENAI_API_KEY_THAT_IS_NOT_SET']\n",
        encoding="utf-8",
    )

    # Ensure the env var is definitely absent
    import os
    os.environ.pop("OPENAI_API_KEY_THAT_IS_NOT_SET", None)

    result = _runner().invoke(config_group, ["validate", "--config", str(config_file)])

    assert result.exit_code == 0, f"Expected success but got:\n{result.output}"
    assert "valid" in result.output.lower()


def test_config_show_toml_succeeds_without_env_secrets(tmp_path):
    """show must not require env: secrets to be present."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[runtime]\noutput_dir = 'docs'\n"
        "[generation]\nmain_model = 'openai/gpt-4o-mini'\ncluster_model = 'openai/gpt-4o-mini'\n"
        "[[providers]]\nname = 'openai'\ntype = 'openai_compatible'\n"
        "model_list = ['gpt-4o-mini']\napi_keys = ['env:OPENAI_API_KEY_THAT_IS_NOT_SET']\n",
        encoding="utf-8",
    )

    import os
    os.environ.pop("OPENAI_API_KEY_THAT_IS_NOT_SET", None)

    result = _runner().invoke(config_group, ["show", "--config", str(config_file)])

    assert result.exit_code == 0, f"Expected success but got:\n{result.output}"
    assert "openai/gpt-4o-mini" in result.output


def test_config_validate_check_secrets_fails_when_env_missing(tmp_path):
    """--check-secrets must fail when a referenced env var is not set."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[runtime]\noutput_dir = 'docs'\n"
        "[generation]\nmain_model = 'openai/gpt-4o-mini'\ncluster_model = 'openai/gpt-4o-mini'\n"
        "[[providers]]\nname = 'openai'\ntype = 'openai_compatible'\n"
        "model_list = ['gpt-4o-mini']\napi_keys = ['env:OPENAI_KEY_DEFINITELY_NOT_SET']\n",
        encoding="utf-8",
    )

    import os
    os.environ.pop("OPENAI_KEY_DEFINITELY_NOT_SET", None)

    result = _runner().invoke(
        config_group, ["validate", "--config", str(config_file), "--check-secrets"]
    )

    assert result.exit_code != 0
    assert "secret" in result.output.lower() or "environment" in result.output.lower()


def test_config_validate_check_secrets_passes_when_env_set(tmp_path, monkeypatch):
    """--check-secrets must pass when every referenced env var is present."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[runtime]\noutput_dir = 'docs'\n"
        "[generation]\nmain_model = 'openai/gpt-4o-mini'\ncluster_model = 'openai/gpt-4o-mini'\n"
        "[[providers]]\nname = 'openai'\ntype = 'openai_compatible'\n"
        "model_list = ['gpt-4o-mini']\napi_keys = ['env:OPENAI_TEST_KEY_FOR_VALIDATE']\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_TEST_KEY_FOR_VALIDATE", "sk-test-value")

    result = _runner().invoke(
        config_group, ["validate", "--config", str(config_file), "--check-secrets"]
    )

    assert result.exit_code == 0
    assert "secret" in result.output.lower()


def test_config_validate_toml_success(tmp_path):
    config_file = _write_toml(tmp_path)

    sentinel = MagicMock()
    sentinel.generation.main_model = "openai/gpt-4o-mini"
    sentinel.generation.cluster_model = "openai/gpt-4o-mini"
    sentinel.generation.fallback_models = []
    sentinel.providers = []

    with patch("codewiki.src.config_loader.load_app_config", return_value=sentinel):
        result = _runner().invoke(
            config_group, ["validate", "--config", str(config_file)]
        )

    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_config_validate_toml_verbose(tmp_path):
    config_file = _write_toml(tmp_path)

    sentinel = MagicMock()
    sentinel.generation.main_model = "openai/gpt-4o-mini"
    sentinel.generation.cluster_model = "openai/gpt-4o-mini"
    sentinel.generation.fallback_models = ["openai/gpt-4o-mini"]
    p = MagicMock()
    p.name = "openai"
    p.type = "openai_compatible"
    sentinel.providers = [p]

    with patch("codewiki.src.config_loader.load_app_config", return_value=sentinel):
        result = _runner().invoke(
            config_group, ["validate", "--config", str(config_file), "--verbose"]
        )

    assert result.exit_code == 0
    assert "openai/gpt-4o-mini" in result.output


def test_config_validate_toml_load_failure(tmp_path):
    config_file = _write_toml(tmp_path)

    with patch("codewiki.src.config_loader.load_app_config", side_effect=ValueError("bad ref")):
        result = _runner().invoke(
            config_group, ["validate", "--config", str(config_file)]
        )

    assert result.exit_code != 0


def test_config_validate_legacy_path_invoked_when_no_config():
    """Without --config, the legacy validation path is taken."""
    from codewiki.cli.commands import config as mod

    with patch.object(mod, "_validate_legacy") as mock_legacy, \
         patch.object(mod, "_validate_toml") as mock_toml:
        _runner().invoke(config_group, ["validate"])

    mock_legacy.assert_called_once()
    mock_toml.assert_not_called()


# ── config show ───────────────────────────────────────────────────────────────

def _make_show_sentinel():
    sentinel = MagicMock()
    sentinel.generation.main_model = "openai/gpt-4o-mini"
    sentinel.generation.cluster_model = "openai/gpt-4o-mini"
    sentinel.generation.fallback_models = []
    sentinel.generation.long_context_model = None
    sentinel.runtime.output_dir = "docs"
    sentinel.runtime.max_depth = 2
    sentinel.runtime.max_concurrent = 3
    sentinel.runtime.max_retries = 2
    sentinel.runtime.output_language = "en"
    sentinel.runtime.postprocess_strict = False
    sentinel.tokens.max_tokens = 32768
    sentinel.tokens.max_token_per_module = 36369
    sentinel.tokens.max_token_per_leaf_module = 16000
    sentinel.tokens.long_context_threshold = 200000
    sentinel.providers = []
    sentinel.agent.to_dict.return_value = {}
    return sentinel


def test_config_show_toml_reads_config_file(tmp_path):
    config_file = _write_toml(tmp_path)

    with patch("codewiki.src.config_loader.load_app_config", return_value=_make_show_sentinel()):
        result = _runner().invoke(
            config_group, ["show", "--config", str(config_file)]
        )

    assert result.exit_code == 0
    assert "openai/gpt-4o-mini" in result.output


def test_config_show_toml_json_output(tmp_path):
    import json
    config_file = _write_toml(tmp_path)

    with patch("codewiki.src.config_loader.load_app_config", return_value=_make_show_sentinel()):
        result = _runner().invoke(
            config_group, ["show", "--config", str(config_file), "--json"]
        )

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["generation"]["main_model"] == "openai/gpt-4o-mini"


def test_config_show_falls_back_to_legacy_without_config():
    """Without --config, _show_legacy is called, not _show_toml."""
    from codewiki.cli.commands import config as mod

    with patch.object(mod, "_show_legacy") as mock_legacy, \
         patch.object(mod, "_show_toml") as mock_toml:
        _runner().invoke(config_group, ["show"])

    mock_legacy.assert_called_once()
    mock_toml.assert_not_called()


# ── config set (deprecated) ───────────────────────────────────────────────────

def test_config_set_shows_deprecation_warning():
    result = _runner().invoke(config_group, ["set", "--help"])
    assert "[Deprecated]" in result.output


def test_config_set_emits_legacy_warning_on_invocation():
    """Running config set (with or without args) prints the legacy warning."""
    result = _runner().invoke(config_group, ["set"])
    # Warning goes to stderr; CliRunner mixes stdout+stderr by default
    assert "deprecated" in result.output.lower() or "deprecated" in (result.output + "").lower()


def test_config_set_warning_contains_config_init_hint():
    result = _runner().invoke(config_group, ["set"])
    assert "config init" in result.output


# ── config agent (deprecated) ─────────────────────────────────────────────────

def test_config_agent_shows_deprecation_warning():
    result = _runner().invoke(config_group, ["agent", "--help"])
    assert "[Deprecated]" in result.output


def test_config_agent_emits_legacy_warning_on_invocation():
    from codewiki.cli.commands import config as mod

    mock_manager = MagicMock()
    mock_manager.load.return_value = True
    mock_manager.get_config.return_value = MagicMock(
        agent_instructions=MagicMock(is_empty=MagicMock(return_value=True))
    )

    with patch.object(mod, "ConfigManager", return_value=mock_manager):
        result = _runner().invoke(config_group, ["agent"])

    assert "deprecated" in result.output.lower()
