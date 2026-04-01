"""
Configuration commands for CodeWiki CLI.
"""

import json
import sys
import click
from pathlib import Path
from typing import Optional, List

from codewiki.cli.config_manager import ConfigManager
from codewiki.cli.models.config import AgentInstructions
from codewiki.cli.utils.errors import (
    ConfigurationError,
    handle_error,
    EXIT_SUCCESS,
    EXIT_CONFIG_ERROR
)
from codewiki.cli.utils.validation import (
    validate_url,
    validate_api_key,
    validate_model_name,
    is_top_tier_model,
    mask_api_key
)

_LEGACY_WARNING = (
    "⚠  'codewiki config set/agent' is deprecated.\n"
    "   Use a TOML config file instead:\n"
    "     codewiki config init          # create a starter config\n"
    "     codewiki generate --config config.toml"
)

# ── Template written by `config init` ────────────────────────────────────────

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
postprocess_strict = false

[tokens]
max_tokens = 32768
max_token_per_module = 36369
max_token_per_leaf_module = 16000
long_context_threshold = 200000

[generation]
main_model = "openai/gpt-4o-mini"
cluster_model = "openai/gpt-4o-mini"
fallback_models = [
  "openai/gpt-4o-mini",
]
# long_context_model = "openai/gpt-4o"

[agent]
# doc_type = "architecture"  # api | architecture | user-guide | developer
# focus_modules = ["src/core", "src/api"]
# custom_instructions = ""

[[providers]]
name = "openai"
type = "openai_compatible"
base_url = "https://api.openai.com/v1"
api_keys = ["env:OPENAI_API_KEY"]
model_list = ["gpt-4o-mini", "gpt-4o"]
extra_headers = {}

# ── Additional provider examples (uncomment to enable) ───────────────────────
#
# [[providers]]
# name = "claude"
# type = "claude"
# api_keys = ["env:ANTHROPIC_API_KEY"]
# anthropic_version = "2024-02-15"
# model_list = ["claude-sonnet-4-5-20250929"]
# extra_headers = {}
#
# [[providers]]
# name = "azure"
# type = "azure_openai"
# base_url = "https://<your-resource>.openai.azure.com"
# api_version = "2024-02-01"
# api_keys = ["env:AZURE_OPENAI_API_KEY"]
# model_list = []
# extra_headers = {}
"""


def parse_patterns(patterns_str: str) -> List[str]:
    """Parse comma-separated patterns into a list."""
    if not patterns_str:
        return []
    return [p.strip() for p in patterns_str.split(',') if p.strip()]


# ── config group ──────────────────────────────────────────────────────────────

@click.group(name="config")
def config_group():
    """Manage CodeWiki configuration."""
    pass


# ── config init ───────────────────────────────────────────────────────────────

@config_group.command(name="init")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    default="config.toml",
    show_default=True,
    help="Destination path for the generated config file.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing file.",
)
def config_init(output: str, force: bool):
    """
    Create a starter config.toml from the built-in template.

    Examples:

    \b
    $ codewiki config init
    $ codewiki config init --output ~/.codewiki/myproject.toml
    $ codewiki config init --force   # overwrite existing file
    """
    dest = Path(output)
    if dest.exists() and not force:
        click.secho(
            f"✗ {dest} already exists. Use --force to overwrite.",
            fg="red",
            err=True,
        )
        sys.exit(EXIT_CONFIG_ERROR)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_TOML_TEMPLATE.replace("{output_path}", str(dest)))

    click.secho(f"✓ Config written to {dest}", fg="green")
    click.echo()
    click.echo("Next steps:")
    click.echo(f"  1. Edit {dest} — fill in your provider API key env vars")
    click.echo(f"  2. Set the env vars referenced in the file (e.g. export OPENAI_API_KEY=sk-...)")
    click.echo(f"  3. codewiki generate --config {dest}")


# ── config validate ───────────────────────────────────────────────────────────

@config_group.command(name="validate")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to TOML config file to validate.",
)
@click.option(
    "--quick",
    is_flag=True,
    help="Skip API connectivity test (TOML path only).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed validation steps.",
)
@click.option(
    "--check-secrets",
    is_flag=True,
    help="Also verify that all env: API key references are set (TOML path only).",
)
def config_validate(config_path: Optional[str], quick: bool, verbose: bool, check_secrets: bool):
    """
    Validate configuration and (optionally) test LLM API connectivity.

    Pass --config to validate a TOML file; omit it to validate the legacy
    ~/.codewiki/config.json (deprecated path).

    By default, env: secret references in api_keys are not resolved so the
    command works on machines that do not hold the secrets.  Use
    --check-secrets to also verify that every referenced env variable is set.

    Examples:

    \b
    $ codewiki config validate --config config.toml
    $ codewiki config validate --config config.toml --check-secrets
    $ codewiki config validate --config config.toml --quick
    $ codewiki config validate --verbose   # legacy path
    """
    try:
        click.echo()
        click.secho("Validating configuration...", fg="blue", bold=True)
        click.echo()

        if config_path:
            _validate_toml(config_path, verbose=verbose, check_secrets=check_secrets)
        else:
            _validate_legacy(quick=quick, verbose=verbose)

    except ConfigurationError as e:
        click.secho(f"\n✗ Configuration error: {e.message}", fg="red", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        sys.exit(handle_error(e, verbose=verbose))


def _validate_toml(config_path: str, verbose: bool, check_secrets: bool = False) -> None:
    from codewiki.src.config_loader import load_app_config

    try:
        app_config = load_app_config(config_path, resolve_secrets=False)
    except Exception as exc:
        click.secho(f"✗ Failed to load TOML config: {exc}", fg="red")
        sys.exit(EXIT_CONFIG_ERROR)

    click.secho(f"✓ TOML parsed: {config_path}", fg="green")

    if verbose:
        click.echo(f"  Main model:    {app_config.generation.main_model}")
        click.echo(f"  Cluster model: {app_config.generation.cluster_model}")
        click.echo(f"  Fallbacks:     {', '.join(app_config.generation.fallback_models)}")
        for p in app_config.providers:
            click.echo(f"  Provider:      {p.name} ({p.type})")

    click.secho(f"✓ All model refs resolved ({len(app_config.providers)} provider(s))", fg="green")

    if check_secrets:
        try:
            load_app_config(config_path, resolve_secrets=True)
            click.secho("✓ All env: secret references are set", fg="green")
        except ValueError as exc:
            click.secho(f"✗ Secret check failed: {exc}", fg="red")
            sys.exit(EXIT_CONFIG_ERROR)

    click.echo()
    click.secho("✓ Configuration is valid!", fg="green", bold=True)
    click.echo()


def _validate_legacy(quick: bool, verbose: bool) -> None:
    manager = ConfigManager()

    if verbose:
        click.echo(f"[1/5] Checking configuration file...")
        click.echo(f"      Path: {manager.config_file_path}")

    if not manager.load():
        click.secho("✗ Configuration file not found", fg="red")
        click.echo()
        click.echo("Run 'codewiki config init' to create a TOML config, then use --config.")
        sys.exit(EXIT_CONFIG_ERROR)

    if verbose:
        click.secho("      ✓ File exists", fg="green")
    else:
        click.secho("✓ Configuration file exists", fg="green")

    api_key = manager.get_api_key()
    if not api_key:
        click.secho("✗ API key missing", fg="red")
        click.echo()
        click.echo("Run 'codewiki config set --api-key <key>' or migrate to a TOML config.")
        sys.exit(EXIT_CONFIG_ERROR)

    if verbose:
        click.secho("      ✓ API key present", fg="green")
    else:
        click.secho("✓ API key present", fg="green")

    config = manager.get_config()

    if not config.base_url:
        click.secho("✗ Base URL not set", fg="red")
        sys.exit(EXIT_CONFIG_ERROR)

    try:
        validate_url(config.base_url)
        click.secho(f"✓ Base URL valid: {config.base_url}", fg="green")
    except ConfigurationError as e:
        click.secho(f"✗ Invalid base URL: {e.message}", fg="red")
        sys.exit(EXIT_CONFIG_ERROR)

    if not config.main_model or not config.cluster_model or not config.fallback_model:
        click.secho("✗ Models not configured", fg="red")
        sys.exit(EXIT_CONFIG_ERROR)

    click.secho(f"✓ Models configured", fg="green")

    if not is_top_tier_model(config.cluster_model):
        click.secho(
            "⚠  Cluster model is not top-tier. Consider claude-sonnet-4 or gpt-4.",
            fg="yellow",
        )

    if not quick:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=config.base_url)
            client.models.list()
            click.secho("✓ API connectivity test successful", fg="green")
        except Exception:
            click.secho("✗ API connectivity test failed", fg="red")
            sys.exit(EXIT_CONFIG_ERROR)

    click.echo()
    click.secho("✓ Configuration is valid!", fg="green", bold=True)
    click.echo()


# ── config show ───────────────────────────────────────────────────────────────

@config_group.command(name="show")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to TOML config file to display.",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output in JSON format.",
)
def config_show(config_path: Optional[str], output_json: bool):
    """
    Display current configuration.

    Pass --config to read a TOML file; omit it to read the legacy
    ~/.codewiki/config.json (deprecated path).

    Examples:

    \b
    $ codewiki config show --config config.toml
    $ codewiki config show --config config.toml --json
    $ codewiki config show   # legacy path
    """
    try:
        if config_path:
            _show_toml(config_path, output_json=output_json)
        else:
            _show_legacy(output_json=output_json)
    except Exception as e:
        sys.exit(handle_error(e))


def _show_toml(config_path: str, output_json: bool) -> None:
    from codewiki.src.config_loader import load_app_config

    try:
        app_config = load_app_config(config_path, resolve_secrets=False)
    except Exception as exc:
        click.secho(f"✗ Failed to load TOML config: {exc}", fg="red", err=True)
        sys.exit(EXIT_CONFIG_ERROR)

    if output_json:
        data = {
            "config_file": config_path,
            "generation": {
                "main_model": app_config.generation.main_model,
                "cluster_model": app_config.generation.cluster_model,
                "fallback_models": app_config.generation.fallback_models,
                "long_context_model": app_config.generation.long_context_model,
            },
            "runtime": {
                "output_dir": app_config.runtime.output_dir,
                "max_depth": app_config.runtime.max_depth,
                "max_concurrent": app_config.runtime.max_concurrent,
                "max_retries": app_config.runtime.max_retries,
                "output_language": app_config.runtime.output_language,
                "postprocess_strict": app_config.runtime.postprocess_strict,
            },
            "tokens": {
                "max_tokens": app_config.tokens.max_tokens,
                "max_token_per_module": app_config.tokens.max_token_per_module,
                "max_token_per_leaf_module": app_config.tokens.max_token_per_leaf_module,
                "long_context_threshold": app_config.tokens.long_context_threshold,
            },
            "providers": [
                {
                    "name": p.name,
                    "type": p.type,
                    "base_url": p.base_url,
                    "model_list": p.model_list,
                }
                for p in app_config.providers
            ],
            "agent": app_config.agent.to_dict(),
        }
        click.echo(json.dumps(data, indent=2))
        return

    click.echo()
    click.secho("CodeWiki Configuration", fg="blue", bold=True)
    click.echo(f"  File: {config_path}")
    click.echo("━" * 40)

    click.echo()
    click.secho("Models", fg="cyan", bold=True)
    click.echo(f"  Main model:         {app_config.generation.main_model}")
    click.echo(f"  Cluster model:      {app_config.generation.cluster_model}")
    click.echo(f"  Fallback models:    {', '.join(app_config.generation.fallback_models) or '—'}")
    if app_config.generation.long_context_model:
        click.echo(f"  Long-context model: {app_config.generation.long_context_model}")

    click.echo()
    click.secho("Providers", fg="cyan", bold=True)
    for p in app_config.providers:
        models = ", ".join(p.model_list) if p.model_list else "any"
        url_part = f"  {p.base_url}" if p.base_url else ""
        click.echo(f"  {p.name} ({p.type}){url_part} — models: {models}")

    click.echo()
    click.secho("Runtime", fg="cyan", bold=True)
    click.echo(f"  Output dir:         {app_config.runtime.output_dir}")
    click.echo(f"  Max depth:          {app_config.runtime.max_depth}")
    click.echo(f"  Max concurrent:     {app_config.runtime.max_concurrent}")
    click.echo(f"  Max retries:        {app_config.runtime.max_retries}")
    click.echo(f"  Output language:    {app_config.runtime.output_language}")

    click.echo()
    click.secho("Tokens", fg="cyan", bold=True)
    click.echo(f"  Max tokens:              {app_config.tokens.max_tokens}")
    click.echo(f"  Max token/module:        {app_config.tokens.max_token_per_module}")
    click.echo(f"  Max token/leaf module:   {app_config.tokens.max_token_per_leaf_module}")
    click.echo(f"  Long-context threshold:  {app_config.tokens.long_context_threshold}")

    agent = app_config.agent.to_dict()
    if agent:
        click.echo()
        click.secho("Agent Instructions", fg="cyan", bold=True)
        for k, v in agent.items():
            click.echo(f"  {k}: {v}")

    click.echo()


def _show_legacy(output_json: bool) -> None:
    manager = ConfigManager()

    if not manager.load():
        click.secho("\n✗ No configuration found.", fg="red", err=True)
        click.echo("\nRun 'codewiki config init' then 'codewiki config show --config config.toml'.")
        sys.exit(EXIT_CONFIG_ERROR)

    config = manager.get_config()
    api_key = manager.get_api_key()

    if output_json:
        output = {
            "api_key": mask_api_key(api_key) if api_key else "Not set",
            "api_key_storage": "keychain" if manager.keyring_available else "encrypted_file",
            "base_url": config.base_url if config else "",
            "main_model": config.main_model if config else "",
            "cluster_model": config.cluster_model if config else "",
            "fallback_model": config.fallback_model if config else "glm-4p5",
            "long_context_model": config.long_context_model if config else "",
            "default_output": config.default_output if config else "docs",
            "max_tokens": config.max_tokens if config else 32768,
            "max_token_per_module": config.max_token_per_module if config else 36369,
            "max_token_per_leaf_module": config.max_token_per_leaf_module if config else 16000,
            "max_depth": config.max_depth if config else 2,
            "max_concurrent": config.max_concurrent if config else 3,
            "agent_instructions": config.agent_instructions.to_dict() if config and config.agent_instructions else {},
            "config_file": str(manager.config_file_path),
            "_legacy": True,
        }
        click.echo(json.dumps(output, indent=2))
        return

    click.echo()
    click.secho("CodeWiki Configuration (legacy)", fg="blue", bold=True)
    click.secho("  ⚠  This is the legacy config.json path.", fg="yellow")
    click.secho("  Run 'codewiki config init' to migrate to a TOML config.", fg="yellow")
    click.echo("━" * 40)
    click.echo()

    click.secho("Credentials", fg="cyan", bold=True)
    if api_key:
        storage = "system keychain" if manager.keyring_available else "encrypted file"
        click.echo(f"  API Key:          {mask_api_key(api_key)} (in {storage})")
    else:
        click.secho("  API Key:          Not set", fg="yellow")

    click.echo()
    click.secho("API Settings", fg="cyan", bold=True)
    if config:
        click.echo(f"  Base URL:         {config.base_url or 'Not set'}")
        click.echo(f"  Main Model:       {config.main_model or 'Not set'}")
        click.echo(f"  Cluster Model:    {config.cluster_model or 'Not set'}")
        click.echo(f"  Fallback Model:   {config.fallback_model or 'Not set'}")
        if config.long_context_model:
            click.echo(f"  Long-Context Model: {config.long_context_model}")

    click.echo()
    click.secho("Token Settings", fg="cyan", bold=True)
    if config:
        click.echo(f"  Max Tokens:              {config.max_tokens}")
        click.echo(f"  Max Token/Module:        {config.max_token_per_module}")
        click.echo(f"  Max Token/Leaf Module:   {config.max_token_per_leaf_module}")

    click.echo()
    click.secho("Decomposition Settings", fg="cyan", bold=True)
    if config:
        click.echo(f"  Max Depth:               {config.max_depth}")
        click.echo(f"  Max Concurrent:          {config.max_concurrent}")

    agent = config.agent_instructions if config else None
    if agent and not agent.is_empty():
        click.echo()
        click.secho("Agent Instructions", fg="cyan", bold=True)
        if agent.include_patterns:
            click.echo(f"  Include patterns:   {', '.join(agent.include_patterns)}")
        if agent.exclude_patterns:
            click.echo(f"  Exclude patterns:   {', '.join(agent.exclude_patterns)}")
        if agent.focus_modules:
            click.echo(f"  Focus modules:      {', '.join(agent.focus_modules)}")
        if agent.doc_type:
            click.echo(f"  Doc type:           {agent.doc_type}")
        if agent.custom_instructions:
            click.echo(f"  Custom instructions: {agent.custom_instructions[:80]}")

    click.echo()
    click.echo(f"Configuration file: {manager.config_file_path}")
    click.echo()


# ── config set (legacy, deprecated) ──────────────────────────────────────────

@config_group.command(name="set")
@click.option("--api-key", type=str, help="LLM API key (stored securely in system keychain)")
@click.option("--base-url", type=str, help="LLM API base URL")
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
    api_key: Optional[str],
    base_url: Optional[str],
    main_model: Optional[str],
    cluster_model: Optional[str],
    fallback_model: Optional[str],
    long_context_model: Optional[str],
    long_context_threshold: Optional[int],
    max_tokens: Optional[int],
    max_token_per_module: Optional[int],
    max_token_per_leaf_module: Optional[int],
    max_depth: Optional[int],
    max_concurrent: Optional[int],
    max_retries: Optional[int],
    language: Optional[str],
):
    """
    [Deprecated] Set persistent configuration values.

    This command writes to ~/.codewiki/config.json and is the legacy
    configuration path.  New users should use a TOML config file instead:

    \b
    $ codewiki config init        # create starter config.toml
    $ codewiki generate --config config.toml

    \b
    # Legacy usage (still works)
    $ codewiki config set --api-key sk-abc123 --base-url https://api.anthropic.com \\
        --main-model claude-sonnet-4 --cluster-model claude-sonnet-4
    """
    click.secho(_LEGACY_WARNING, fg="yellow", err=True)
    click.echo()

    try:
        if not any([
            api_key, base_url, main_model, cluster_model, fallback_model,
            long_context_model, long_context_threshold, max_tokens,
            max_token_per_module, max_token_per_leaf_module, max_depth,
            max_concurrent, max_retries, language,
        ]):
            click.echo("No options provided. Use --help for usage information.")
            sys.exit(EXIT_CONFIG_ERROR)

        validated_data = {}

        if api_key:
            validated_data['api_key'] = validate_api_key(api_key)
        if base_url:
            validated_data['base_url'] = validate_url(base_url)
        if main_model:
            validated_data['main_model'] = validate_model_name(main_model)
        if cluster_model:
            validated_data['cluster_model'] = validate_model_name(cluster_model)
        if fallback_model:
            for name in fallback_model.split(","):
                validate_model_name(name.strip())
            validated_data['fallback_model'] = fallback_model
        if long_context_model:
            validated_data['long_context_model'] = validate_model_name(long_context_model)
        if long_context_threshold is not None:
            if long_context_threshold < 1:
                raise ConfigurationError("long_context_threshold must be a positive integer")
            validated_data['long_context_threshold'] = long_context_threshold
        if max_tokens is not None:
            if max_tokens < 1:
                raise ConfigurationError("max_tokens must be a positive integer")
            validated_data['max_tokens'] = max_tokens
        if max_token_per_module is not None:
            if max_token_per_module < 1:
                raise ConfigurationError("max_token_per_module must be a positive integer")
            validated_data['max_token_per_module'] = max_token_per_module
        if max_token_per_leaf_module is not None:
            if max_token_per_leaf_module < 1:
                raise ConfigurationError("max_token_per_leaf_module must be a positive integer")
            validated_data['max_token_per_leaf_module'] = max_token_per_leaf_module
        if max_depth is not None:
            if max_depth < 1:
                raise ConfigurationError("max_depth must be a positive integer")
            validated_data['max_depth'] = max_depth
        if max_concurrent is not None:
            if max_concurrent < 1:
                raise ConfigurationError("max_concurrent must be a positive integer")
            validated_data['max_concurrent'] = max_concurrent
        if max_retries is not None:
            if max_retries < 0:
                raise ConfigurationError("max_retries must be a non-negative integer")
            validated_data['max_retries'] = max_retries
        if language is not None:
            validated_data['output_language'] = language.strip().lower()

        manager = ConfigManager()
        manager.load()
        manager.save(
            api_key=validated_data.get('api_key'),
            base_url=validated_data.get('base_url'),
            main_model=validated_data.get('main_model'),
            cluster_model=validated_data.get('cluster_model'),
            fallback_model=validated_data.get('fallback_model'),
            long_context_model=validated_data.get('long_context_model'),
            long_context_threshold=validated_data.get('long_context_threshold'),
            max_tokens=validated_data.get('max_tokens'),
            max_token_per_module=validated_data.get('max_token_per_module'),
            max_token_per_leaf_module=validated_data.get('max_token_per_leaf_module'),
            max_depth=validated_data.get('max_depth'),
            max_concurrent=validated_data.get('max_concurrent'),
            max_retries=validated_data.get('max_retries'),
            output_language=validated_data.get('output_language'),
        )

        click.echo()
        if api_key:
            if manager.keyring_available:
                click.secho("✓ API key saved to system keychain", fg="green")
            else:
                click.secho(
                    f"⚠  Keychain unavailable. API key saved to {manager.config_file_path} (plaintext).",
                    fg="yellow",
                )
                click.secho(
                    f"   chmod 600 {manager.config_file_path}",
                    fg="yellow",
                )
        if base_url:
            click.secho(f"✓ Base URL: {base_url}", fg="green")
        if main_model:
            click.secho(f"✓ Main model: {main_model}", fg="green")
        if cluster_model:
            click.secho(f"✓ Cluster model: {cluster_model}", fg="green")
            if not is_top_tier_model(cluster_model):
                click.secho("⚠  Cluster model is not top-tier.", fg="yellow")
        if fallback_model:
            click.secho(f"✓ Fallback model: {fallback_model}", fg="green")
        if long_context_model:
            click.secho(f"✓ Long-context model: {long_context_model}", fg="green")
        if max_tokens:
            click.secho(f"✓ Max tokens: {max_tokens}", fg="green")
        if max_concurrent:
            click.secho(f"✓ Max concurrent: {max_concurrent}", fg="green")
        if max_retries is not None:
            click.secho(f"✓ Max retries: {max_retries}", fg="green")
        if language:
            click.secho(f"✓ Output language: {language}", fg="green")

        click.echo("\n" + click.style("Configuration updated successfully.", fg="green", bold=True))

    except ConfigurationError as e:
        click.secho(f"\n✗ Configuration error: {e.message}", fg="red", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        sys.exit(handle_error(e))


# ── config agent (legacy, deprecated) ────────────────────────────────────────

@config_group.command(name="agent")
@click.option("--include", "-i", type=str, default=None,
              help="Comma-separated file patterns to include")
@click.option("--exclude", "-e", type=str, default=None,
              help="Comma-separated patterns to exclude")
@click.option("--focus", "-f", type=str, default=None,
              help="Comma-separated modules/paths to focus on")
@click.option("--doc-type", "-t",
              type=click.Choice(['api', 'architecture', 'user-guide', 'developer'],
                                case_sensitive=False),
              default=None,
              help="Default type of documentation to generate")
@click.option("--instructions", type=str, default=None,
              help="Custom instructions for the documentation agent")
@click.option("--clear", is_flag=True, help="Clear all agent instructions")
def config_agent(
    include: Optional[str],
    exclude: Optional[str],
    focus: Optional[str],
    doc_type: Optional[str],
    instructions: Optional[str],
    clear: bool,
):
    """
    [Deprecated] Configure default agent instructions in config.json.

    Prefer setting agent instructions in the [agent] section of your TOML
    config file instead.

    \b
    $ codewiki config init   # creates a config.toml with an [agent] section
    """
    click.secho(_LEGACY_WARNING, fg="yellow", err=True)
    click.echo()

    try:
        manager = ConfigManager()

        if not manager.load():
            click.secho("\n✗ Configuration not found.", fg="red", err=True)
            click.echo("\nRun 'codewiki config init' to create a TOML config.")
            sys.exit(EXIT_CONFIG_ERROR)

        config = manager.get_config()

        if clear:
            config.agent_instructions = AgentInstructions()
            manager.save()
            click.echo()
            click.secho("✓ Agent instructions cleared", fg="green")
            click.echo()
            return

        if not any([include, exclude, focus, doc_type, instructions]):
            click.echo()
            click.secho("Agent Instructions", fg="blue", bold=True)
            click.echo("━" * 40)
            click.echo()
            agent = config.agent_instructions
            if agent and not agent.is_empty():
                if agent.include_patterns:
                    click.echo(f"  Include patterns:   {', '.join(agent.include_patterns)}")
                if agent.exclude_patterns:
                    click.echo(f"  Exclude patterns:   {', '.join(agent.exclude_patterns)}")
                if agent.focus_modules:
                    click.echo(f"  Focus modules:      {', '.join(agent.focus_modules)}")
                if agent.doc_type:
                    click.echo(f"  Doc type:           {agent.doc_type}")
                if agent.custom_instructions:
                    click.echo(f"  Custom instructions: {agent.custom_instructions}")
            else:
                click.secho("  No agent instructions configured", fg="yellow")
            click.echo()
            click.echo("Use 'codewiki config agent --help' for usage information.")
            click.echo()
            return

        current = config.agent_instructions or AgentInstructions()

        if include is not None:
            current.include_patterns = parse_patterns(include) if include else None
        if exclude is not None:
            current.exclude_patterns = parse_patterns(exclude) if exclude else None
        if focus is not None:
            current.focus_modules = parse_patterns(focus) if focus else None
        if doc_type is not None:
            current.doc_type = doc_type if doc_type else None
        if instructions is not None:
            current.custom_instructions = instructions if instructions else None

        config.agent_instructions = current
        manager.save()

        click.echo()
        if include:
            click.secho(f"✓ Include patterns: {parse_patterns(include)}", fg="green")
        if exclude:
            click.secho(f"✓ Exclude patterns: {parse_patterns(exclude)}", fg="green")
        if focus:
            click.secho(f"✓ Focus modules: {parse_patterns(focus)}", fg="green")
        if doc_type:
            click.secho(f"✓ Doc type: {doc_type}", fg="green")
        if instructions:
            click.secho("✓ Custom instructions set", fg="green")

        click.echo("\n" + click.style("Agent instructions updated.", fg="green", bold=True))
        click.echo()

    except ConfigurationError as e:
        click.secho(f"\n✗ Configuration error: {e.message}", fg="red", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        sys.exit(handle_error(e))
