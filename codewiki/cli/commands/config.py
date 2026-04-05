"""Configuration commands for CodeWiki CLI."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import click
import structlog
import tomli_w

from codewiki.cli.utils.errors import (
    ConfigurationError,
    EXIT_CONFIG_ERROR,
    handle_error,
)
from codewiki.src.be.llm_services import validate_llm_credentials
from codewiki.src.codewiki_config import CodeWikiConfig
from codewiki.src.config_loader import load_config
from codewiki.src.logging_setup import configure_cli_logging

_logger = structlog.get_logger("codewiki.cli.config")

_API_KEY_BREAKING_CHANGE = (
    "Legacy keyring support was removed.\n\n"
    "Put provider API keys in config.toml using env:VAR syntax under [[providers]].\n"
    'Example: api_keys = ["env:OPENAI_API_KEY"]'
)

_TOML_TEMPLATE = """\
# CodeWiki configuration file
# Run: codewiki generate --config {output_path}
#
# Provider/model references use the format: "provider_name/model_name"
# API keys use env references: "env:YOUR_ENV_VAR_NAME"

[runtime]
output_dir = "docs"
max_depth = 2
max_concurrent = 3
max_retries = 2
output_language = "en"

[tokens]
max_tokens = 32768
max_token_per_module = 36369
max_token_per_leaf_module = 16000
long_context_threshold = 200000
max_input_tokens = 800000           # source code truncation budget per module prompt
long_context_max_input_tokens = 800000  # token limit for long-context model requests

[generation]
main_model = "openai/gpt-4o-mini"
cluster_model = "openai/gpt-4o-mini"
fallback_models = [
  "openai/gpt-4o-mini",
]
# long_context_model = "openai/gpt-4o"
# long_context_fallback = ["openai/gpt-4o-mini"]  # fallback chain if long-context model fails

[agent]
# doc_type = "architecture"  # api | architecture | user-guide | developer
# focus_modules = ["src/core", "src/api"]
# custom_instructions = ""

[postprocess]
strict = false
fix_links = true
degrade_mermaid = false  # true = replace unfixable mermaid with text blocks
# repair_model = ""
# repair_fallback_1 = ""
# repair_fallback_2 = ""
# repair_batch_size = 8
# repair_max_retries = 2

[[providers]]
name = "openai"
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_keys = ["env:OPENAI_API_KEY"]
model_list = ["gpt-4o-mini", "gpt-4o"]
extra_headers = {}
"""


def parse_patterns(patterns_str: str) -> list[str]:
    """Parse comma-separated patterns into a list."""
    if not patterns_str:
        return []
    return [p.strip() for p in patterns_str.split(",") if p.strip()]


def _resolve_config_path(config_path: str | None, *, must_exist: bool = True) -> Path:
    path = Path(config_path) if config_path else Path("config.toml")
    if must_exist and not path.exists():
        raise ConfigurationError(
            f"Config file not found: {path}\n\nRun `codewiki config init` to create config.toml."
        )
    return path


def _read_toml_data(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _write_toml_data(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def _load_runtime_config(config_path: Path, *, resolve_secrets: bool = False) -> CodeWikiConfig:
    return load_config(
        config_path,
        repo_path=str(Path.cwd()),
        context="cli",
        resolve_secrets=resolve_secrets,
    )


def _config_to_dict(config_path: str, cfg: CodeWikiConfig) -> dict[str, Any]:
    fallback_models = [item.strip() for item in cfg.fallback_model if item.strip()]
    return {
        "config_file": config_path,
        "generation": {
            "main_model": cfg.main_model,
            "cluster_model": cfg.cluster_model,
            "fallback_models": fallback_models,
            "long_context_model": cfg.long_context_model,
        },
        "runtime": {
            "output_dir": cfg.docs_dir,
            "max_depth": cfg.max_depth,
            "max_concurrent": cfg.max_concurrent,
            "max_retries": cfg.max_retries,
            "output_language": cfg.output_language,
        },
        "postprocess": {
            "strict": cfg.postprocess.strict,
            "fix_links": cfg.postprocess.fix_links,
            "repair_model": cfg.postprocess.repair_model,
            "repair_fallback_1": cfg.postprocess.repair_fallback_1,
            "repair_fallback_2": cfg.postprocess.repair_fallback_2,
            "repair_batch_size": cfg.postprocess.repair_batch_size,
            "repair_max_retries": cfg.postprocess.repair_max_retries,
        },
        "tokens": {
            "max_tokens": cfg.max_tokens,
            "max_token_per_module": cfg.max_token_per_module,
            "max_token_per_leaf_module": cfg.max_token_per_leaf_module,
            "long_context_threshold": cfg.long_context_threshold,
        },
        "providers": [
            {
                "name": provider.name,
                "type": provider.type,
                "base_url": provider.base_url,
                "endpoint": provider.endpoint,
                "model_list": list(provider.model_list),
            }
            for provider in cfg.providers
        ],
        "agent": dict(cfg.agent_instructions or {}),
    }


def _lookup_config_value(payload: dict[str, Any], key: str) -> Any:
    current: Any = payload
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        raise ConfigurationError(f"Unknown config key: {key}")
    return current


def _render_config(payload: dict[str, Any]) -> None:
    _logger.info("CodeWiki Configuration", config_file=payload["config_file"])
    _logger.info("Models", **payload["generation"])
    _logger.info("Runtime", **payload["runtime"])
    _logger.info("Postprocess", **payload["postprocess"])
    _logger.info("Tokens", **payload["tokens"])
    for provider in payload["providers"]:
        _logger.info("Provider", **provider)
    if payload["agent"]:
        _logger.info("Agent instructions", **payload["agent"])


def _first_provider(data: dict[str, Any]) -> dict[str, Any]:
    providers = data.setdefault("providers", [])
    if not providers:
        raise ConfigurationError("No [[providers]] entries found in config.toml.")
    provider = providers[0]
    if len(providers) > 1:
        _logger.warning(
            "Multiple providers configured; applying update to the first provider only",
            provider=provider.get("name"),
        )
    return provider


@click.group(name="config")
def config_group():
    """Manage CodeWiki configuration."""


@config_group.command(name="init")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    default="config.toml",
    show_default=True,
    help="Destination path for the generated config file.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing file.")
def config_init(output: str, force: bool):
    """Create a starter config.toml from the built-in template."""
    configure_cli_logging(verbose=False)
    dest = Path(output)
    if dest.exists() and not force:
        _logger.error("Config already exists; use --force to overwrite", path=str(dest))
        raise SystemExit(EXIT_CONFIG_ERROR)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_TOML_TEMPLATE.replace("{output_path}", str(dest)), encoding="utf-8")

    _logger.info("Config written", path=str(dest))
    _logger.info("Next steps")
    _logger.info("1. Edit the config file and fill in provider API key env vars", path=str(dest))
    _logger.info("2. Export the referenced env vars")
    _logger.info(f"3. Run codewiki generate --config {dest}")


@config_group.command(name="gen")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    default="config.toml",
    show_default=True,
    help="Path to embed in the generated template comments.",
)
def config_gen(output: str):
    """Print the starter config template to stdout."""
    click.echo(_TOML_TEMPLATE.replace("{output_path}", output), nl=False)


@config_group.command(name="validate")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to TOML config file to validate.",
)
@click.option(
    "--quick", is_flag=True, help="Accepted for compatibility; connectivity probing is skipped."
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed validation steps.")
@click.option(
    "--check-secrets",
    is_flag=True,
    help="Also verify that every env: API key reference is currently set.",
)
def config_validate(config_path: str | None, quick: bool, verbose: bool, check_secrets: bool):
    """Validate a TOML configuration file."""
    del quick
    configure_cli_logging(verbose=verbose)
    try:
        path = _resolve_config_path(config_path)
        _logger.info("Validating configuration", path=str(path))
        cfg = _load_runtime_config(path, resolve_secrets=True)
        _logger.info("Model references resolved", providers=len(cfg.providers))
        if verbose:
            _logger.info(
                "Effective models",
                main_model=cfg.main_model,
                cluster_model=cfg.cluster_model,
                fallback_model=cfg.fallback_model,
            )

        validate_llm_credentials(cfg)
        if check_secrets:
            _logger.info("All env: secret references are set")
        else:
            _logger.info("All provider credentials resolved")

        _logger.info("Configuration is valid")
    except Exception as exc:
        raise SystemExit(handle_error(exc, verbose=verbose))


@config_group.command(name="get")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to TOML config file to display.",
)
@click.option("--json", "output_json", is_flag=True, help="Output in JSON format.")
@click.argument("key", required=False)
def config_get(config_path: str | None, output_json: bool, key: str | None):
    """Display current TOML configuration or a specific key."""
    configure_cli_logging(verbose=False)
    try:
        path = _resolve_config_path(config_path)
        cfg = _load_runtime_config(path, resolve_secrets=False)
        payload = _config_to_dict(str(path), cfg)
        if key:
            value = _lookup_config_value(payload, key)
            if output_json or isinstance(value, (dict, list)):
                click.echo(json.dumps(value, indent=2))
            else:
                click.echo(str(value))
            return
        if output_json:
            click.echo(json.dumps(payload, indent=2))
            return

        _render_config(payload)
    except Exception as exc:
        raise SystemExit(handle_error(exc))


@config_group.command(name="show", hidden=True)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to TOML config file to display.",
)
@click.option("--json", "output_json", is_flag=True, help="Output in JSON format.")
def config_show(config_path: str | None, output_json: bool):
    """Backward-compatible alias for `config get`."""
    configure_cli_logging(verbose=False)
    try:
        path = _resolve_config_path(config_path)
        cfg = _load_runtime_config(path, resolve_secrets=False)
        payload = _config_to_dict(str(path), cfg)
        if output_json:
            click.echo(json.dumps(payload, indent=2))
            return

        _render_config(payload)
    except Exception as exc:
        raise SystemExit(handle_error(exc))


@config_group.command(name="set")
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default="config.toml")
@click.option("--api-key", type=str, help="Removed. Use env:VAR in [[providers]].")
@click.option("--base-url", type=str, help="LLM API base URL for the first provider")
@click.option("--main-model", type=str, help="Primary model for documentation generation")
@click.option("--cluster-model", type=str, help="Model for module clustering")
@click.option("--fallback-model", type=str, help="Fallback model(s) (comma-separated)")
@click.option("--long-context-model", type=str, help="Model for long-context prompts")
@click.option("--long-context-threshold", type=int, help="Token threshold for long-context model")
@click.option("--max-tokens", type=int, help="Maximum tokens for LLM response")
@click.option("--max-token-per-module", type=int, help="Maximum tokens per module")
@click.option("--max-token-per-leaf-module", type=int, help="Maximum tokens per leaf module")
@click.option("--max-depth", type=int, help="Maximum depth for hierarchical decomposition")
@click.option("--max-concurrent", type=int, help="Maximum modules processed concurrently")
@click.option("--max-retries", type=int, help="Fill-pass retries for missing module docs")
@click.option("--language", type=str, default=None, help="Language for generated documentation")
def config_set(
    config_path: str,
    api_key: str | None,
    base_url: str | None,
    main_model: str | None,
    cluster_model: str | None,
    fallback_model: str | None,
    long_context_model: str | None,
    long_context_threshold: int | None,
    max_tokens: int | None,
    max_token_per_module: int | None,
    max_token_per_leaf_module: int | None,
    max_depth: int | None,
    max_concurrent: int | None,
    max_retries: int | None,
    language: str | None,
):
    """Update TOML configuration values."""
    configure_cli_logging(verbose=False)
    try:
        if api_key:
            raise ConfigurationError(_API_KEY_BREAKING_CHANGE)
        if not any(
            [
                base_url,
                main_model,
                cluster_model,
                fallback_model,
                long_context_model,
                long_context_threshold,
                max_tokens,
                max_token_per_module,
                max_token_per_leaf_module,
                max_depth,
                max_concurrent,
                max_retries,
                language,
            ]
        ):
            raise ConfigurationError("No options provided. Use --help for usage information.")

        path = _resolve_config_path(config_path)
        data = _read_toml_data(path)
        runtime = data.setdefault("runtime", {})
        tokens = data.setdefault("tokens", {})
        generation = data.setdefault("generation", {})

        if base_url is not None:
            _first_provider(data)["base_url"] = base_url
        if main_model is not None:
            generation["main_model"] = main_model
        if cluster_model is not None:
            generation["cluster_model"] = cluster_model
        if fallback_model is not None:
            generation["fallback_models"] = parse_patterns(fallback_model)
        if long_context_model is not None:
            generation["long_context_model"] = long_context_model
        if long_context_threshold is not None:
            tokens["long_context_threshold"] = long_context_threshold
        if max_tokens is not None:
            tokens["max_tokens"] = max_tokens
        if max_token_per_module is not None:
            tokens["max_token_per_module"] = max_token_per_module
        if max_token_per_leaf_module is not None:
            tokens["max_token_per_leaf_module"] = max_token_per_leaf_module
        if max_depth is not None:
            runtime["max_depth"] = max_depth
        if max_concurrent is not None:
            runtime["max_concurrent"] = max_concurrent
        if max_retries is not None:
            runtime["max_retries"] = max_retries
        if language is not None:
            runtime["output_language"] = language.strip().lower()

        _write_toml_data(path, data)
        cfg = _load_runtime_config(path, resolve_secrets=False)
        validate_llm_credentials(cfg)
        _logger.info("Configuration updated", path=str(path))
    except Exception as exc:
        raise SystemExit(handle_error(exc))


@config_group.command(name="agent")
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default="config.toml")
@click.option(
    "--include", "-i", type=str, default=None, help="Comma-separated file patterns to include"
)
@click.option("--exclude", "-e", type=str, default=None, help="Comma-separated patterns to exclude")
@click.option(
    "--focus", "-f", type=str, default=None, help="Comma-separated modules/paths to focus on"
)
@click.option(
    "--doc-type",
    "-t",
    type=click.Choice(["api", "architecture", "user-guide", "developer"], case_sensitive=False),
    default=None,
    help="Default type of documentation to generate",
)
@click.option(
    "--instructions", type=str, default=None, help="Custom instructions for the documentation agent"
)
@click.option("--clear", is_flag=True, help="Clear all agent instructions")
def config_agent(
    config_path: str,
    include: str | None,
    exclude: str | None,
    focus: str | None,
    doc_type: str | None,
    instructions: str | None,
    clear: bool,
):
    """Read or update the [agent] section in config.toml."""
    configure_cli_logging(verbose=False)
    try:
        path = _resolve_config_path(config_path)
        data = _read_toml_data(path)
        if clear:
            data.pop("agent", None)
            _write_toml_data(path, data)
            _logger.info("Agent instructions cleared", path=str(path))
            return

        if not any([include, exclude, focus, doc_type, instructions]):
            cfg = _load_runtime_config(path, resolve_secrets=False)
            if cfg.agent_instructions:
                _logger.info("Agent instructions", **cfg.agent_instructions)
            else:
                _logger.info("No agent instructions configured", path=str(path))
            return

        agent = dict(cast(dict[str, Any], data.get("agent", {})))
        if include is not None:
            agent["include_patterns"] = parse_patterns(include)
        if exclude is not None:
            agent["exclude_patterns"] = parse_patterns(exclude)
        if focus is not None:
            agent["focus_modules"] = parse_patterns(focus)
        if doc_type is not None:
            agent["doc_type"] = doc_type
        if instructions is not None:
            agent["custom_instructions"] = instructions
        data["agent"] = {key: value for key, value in agent.items() if value not in (None, [], "")}
        _write_toml_data(path, data)
        _logger.info("Agent instructions updated", path=str(path))
    except Exception as exc:
        raise SystemExit(handle_error(exc))
