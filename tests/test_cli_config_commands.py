"""
Tests for the config subcommands:
  - config init  (create TOML template)
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
