"""
Tests for the config subcommands:
  - config init  (create TOML template)
  - config gen   (print TOML template to stdout)
  - config get   (TOML path only)
  - config validate (TOML path only)
  - config set   (TOML editing)
  - config agent (TOML editing)
"""

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from codewiki.cli.commands.config import config_group, _TOML_TEMPLATE


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


# ── config gen ────────────────────────────────────────────────────────────────


def test_config_gen_prints_template_to_stdout():
    result = _runner().invoke(config_group, ["gen"])

    assert result.exit_code == 0
    assert "[runtime]" in result.output
    assert "[generation]" in result.output
    assert "[[providers]]" in result.output
    assert "config.toml" in result.output


def test_config_gen_allows_custom_output_path_in_template():
    result = _runner().invoke(config_group, ["gen", "--output", "nested/custom.toml"])

    assert result.exit_code == 0
    assert "nested/custom.toml" in result.output


# ── config validate ───────────────────────────────────────────────────────────


def test_config_validate_requires_env_secrets_by_default(tmp_path):
    """validate must fail when referenced env: secrets are missing."""
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

    assert result.exit_code != 0
    assert "secret" in result.output.lower() or "environment" in result.output.lower()


def test_config_get_toml_succeeds_without_env_secrets(tmp_path):
    """get must not require env: secrets to be present."""
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

    result = _runner().invoke(config_group, ["get", "--config", str(config_file)])

    assert result.exit_code == 0, f"Expected success but got:\n{result.output}"
    assert "openai/gpt-4o-mini" in result.output


def test_config_validate_check_secrets_fails_when_env_missing(tmp_path):
    """--check-secrets remains compatible and still fails when env vars are missing."""
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


def test_config_validate_passes_when_env_set_by_default(tmp_path, monkeypatch):
    """validate should check provider credentials even without --check-secrets."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[runtime]\noutput_dir = 'docs'\n"
        "[generation]\nmain_model = 'openai/gpt-4o-mini'\ncluster_model = 'openai/gpt-4o-mini'\n"
        "[[providers]]\nname = 'openai'\ntype = 'openai_compatible'\n"
        "model_list = ['gpt-4o-mini']\napi_keys = ['env:OPENAI_VALIDATE_DEFAULT']\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_VALIDATE_DEFAULT", "sk-test-value")

    result = _runner().invoke(config_group, ["validate", "--config", str(config_file)])

    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_config_validate_toml_success(tmp_path):
    config_file = _write_toml(tmp_path)

    from codewiki.src.codewiki_config import CodeWikiConfig

    sentinel = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="docs",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
    )

    with (
        patch("codewiki.cli.commands.config.load_config", return_value=sentinel),
        patch("codewiki.cli.commands.config.validate_llm_credentials"),
    ):
        result = _runner().invoke(config_group, ["validate", "--config", str(config_file)])

    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_config_validate_toml_verbose(tmp_path):
    config_file = _write_toml(tmp_path)

    from codewiki.src.codewiki_config import CodeWikiConfig, ProviderConfig

    sentinel = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="docs",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        providers=[
            ProviderConfig(
                name="openai",
                type="openai_compatible",
                model_list=["gpt-4o-mini"],
            )
        ],
    )

    with (
        patch("codewiki.cli.commands.config.load_config", return_value=sentinel),
        patch("codewiki.cli.commands.config.validate_llm_credentials"),
    ):
        result = _runner().invoke(
            config_group, ["validate", "--config", str(config_file), "--verbose"]
        )

    assert result.exit_code == 0
    assert "openai/gpt-4o-mini" in result.output


def test_config_validate_toml_load_failure(tmp_path):
    config_file = _write_toml(tmp_path)

    with patch("codewiki.cli.commands.config.load_config", side_effect=ValueError("bad ref")):
        result = _runner().invoke(config_group, ["validate", "--config", str(config_file)])

    assert result.exit_code != 0


# ── config get ────────────────────────────────────────────────────────────────


def _make_get_sentinel():
    from codewiki.src.codewiki_config import CodeWikiConfig, ProviderConfig

    return CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="docs",
        output_dir="docs/temp",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        providers=[
            ProviderConfig(
                name="openai",
                type="openai_compatible",
                model_list=["gpt-4o-mini"],
            )
        ],
    )


def test_config_get_toml_reads_config_file(tmp_path):
    config_file = _write_toml(tmp_path)

    with patch("codewiki.cli.commands.config.load_config", return_value=_make_get_sentinel()):
        result = _runner().invoke(config_group, ["get", "--config", str(config_file)])

    assert result.exit_code == 0
    assert "openai/gpt-4o-mini" in result.output


def test_config_get_toml_json_output(tmp_path):
    import json

    config_file = _write_toml(tmp_path)

    with patch("codewiki.cli.commands.config.load_config", return_value=_make_get_sentinel()):
        result = _runner().invoke(config_group, ["get", "--config", str(config_file), "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["generation"]["main_model"] == "openai/gpt-4o-mini"


def test_config_get_specific_key(tmp_path):
    config_file = _write_toml(tmp_path)

    with patch("codewiki.cli.commands.config.load_config", return_value=_make_get_sentinel()):
        result = _runner().invoke(
            config_group, ["get", "--config", str(config_file), "generation.main_model"]
        )

    assert result.exit_code == 0
    assert result.output.strip() == "openai/gpt-4o-mini"


def test_config_get_unknown_key_fails(tmp_path):
    config_file = _write_toml(tmp_path)

    with patch("codewiki.cli.commands.config.load_config", return_value=_make_get_sentinel()):
        result = _runner().invoke(
            config_group, ["get", "--config", str(config_file), "generation.missing"]
        )

    assert result.exit_code != 0
    assert "unknown config key" in result.output.lower()


def test_config_set_updates_toml_values(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[runtime]\n"
        "output_dir = 'docs'\n"
        "[generation]\n"
        "main_model = 'openai/gpt-4o-mini'\n"
        "cluster_model = 'openai/gpt-4o-mini'\n"
        "[[providers]]\n"
        "name = 'openai'\n"
        "type = 'openai_compatible'\n"
        "model_list = ['gpt-4o-mini', 'gpt-4o']\n"
        "api_keys = []\n",
        encoding="utf-8",
    )

    with patch("codewiki.cli.commands.config.validate_llm_credentials"):
        result = _runner().invoke(
            config_group,
            [
                "set",
                "--config",
                str(config_file),
                "--main-model",
                "openai/gpt-4o",
                "--fallback-model",
                "openai/gpt-4o,openai/gpt-4o-mini",
                "--language",
                "ZH",
                "--max-concurrent",
                "5",
            ],
        )

    assert result.exit_code == 0
    content = config_file.read_text(encoding="utf-8")
    assert 'main_model = "openai/gpt-4o"' in content
    assert "fallback_models = [" in content
    assert '"openai/gpt-4o"' in content
    assert '"openai/gpt-4o-mini"' in content
    assert 'output_language = "zh"' in content
    assert "max_concurrent = 5" in content


def test_config_set_requires_options(tmp_path):
    config_file = _write_toml(tmp_path)

    result = _runner().invoke(config_group, ["set", "--config", str(config_file)])

    assert result.exit_code != 0
    assert "no options provided" in result.output.lower()


def test_config_set_rejects_api_key_flag(tmp_path):
    config_file = _write_toml(tmp_path)

    result = _runner().invoke(
        config_group,
        ["set", "--config", str(config_file), "--api-key", "sk-test"],
    )

    assert result.exit_code != 0
    assert "keyring support was removed" in result.output.lower()


def test_config_agent_updates_patterns_and_doc_type(tmp_path):
    config_file = _write_toml(tmp_path)

    result = _runner().invoke(
        config_group,
        [
            "agent",
            "--config",
            str(config_file),
            "--include",
            "src/*.py,tests/*.py",
            "--focus",
            "src/core,src/api",
            "--doc-type",
            "architecture",
            "--instructions",
            "Focus on public APIs",
        ],
    )

    assert result.exit_code == 0
    content = config_file.read_text(encoding="utf-8")
    assert "[agent]" in content
    assert "include_patterns = [" in content
    assert '"src/*.py"' in content
    assert '"tests/*.py"' in content
    assert "focus_modules = [" in content
    assert '"src/core"' in content
    assert '"src/api"' in content
    assert 'doc_type = "architecture"' in content
    assert 'custom_instructions = "Focus on public APIs"' in content


def test_config_agent_clear_removes_section(tmp_path):
    config_file = _write_toml(tmp_path)
    config_file.write_text(
        config_file.read_text(encoding="utf-8") + "\n[agent]\nfocus_modules = ['src/core']\n",
        encoding="utf-8",
    )

    result = _runner().invoke(config_group, ["agent", "--config", str(config_file), "--clear"])

    assert result.exit_code == 0
    assert "[agent]" not in config_file.read_text(encoding="utf-8")


def test_config_agent_reads_existing_instructions(tmp_path):
    config_file = _write_toml(tmp_path)
    config_file.write_text(
        config_file.read_text(encoding="utf-8")
        + "\n[agent]\nfocus_modules = ['src/core']\ncustom_instructions = 'Be concise'\n",
        encoding="utf-8",
    )

    result = _runner().invoke(config_group, ["agent", "--config", str(config_file)])

    assert result.exit_code == 0
    assert "focus_modules" in result.output
    assert "Be concise" in result.output
