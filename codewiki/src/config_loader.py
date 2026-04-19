from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, cast
import os
import tomllib

from codewiki.src.codewiki_config import (
    CodeWikiConfig,
    IncrementalConfig,
    PostprocessConfig,
    ProviderConfig,
    RefinementConfig,
)

DEFAULT_MAX_TOKENS = 32_768
DEFAULT_MAX_TOKEN_PER_MODULE = 36_369
DEFAULT_MAX_TOKEN_PER_LEAF_MODULE = 16_000
DEFAULT_MAX_CONCURRENT = 3
DEFAULT_MAX_RETRIES = 2
DEFAULT_LONG_CONTEXT_THRESHOLD = 200_000
MAX_DEPTH = 2
DEPENDENCY_GRAPHS_DIR = "dependency_graphs"


_DEFAULT_PROVIDER_NAMES = {
    "openai",
    "claude",
    "azure_openai",
    "gemini",
    "gemini_ai_studio",
    "dashscope",
    "ollama",
}


@dataclass
class RuntimeOverrides:
    output_dir: Optional[str] = None
    max_depth: Optional[int] = None
    max_tokens: Optional[int] = None
    max_token_per_module: Optional[int] = None
    max_token_per_leaf_module: Optional[int] = None
    max_concurrent: Optional[int] = None
    max_retries: Optional[int] = None
    output_language: Optional[str] = None
    main_model: Optional[str] = None
    cluster_model: Optional[str] = None
    fallback_models: Optional[list[str]] = None
    long_context_model: Optional[str] = None
    long_context_fallback: Optional[list[str]] = None
    long_context_threshold: Optional[int] = None
    agent_instructions: Optional[dict[str, Any]] = None


@dataclass
class ResolvedModel:
    provider_name: str
    model_name: str
    provider: Optional[ProviderConfig] = None
    credential_source: Optional[str] = None
    stream: bool = False


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def _resolve_env_ref(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("env:"):
        env_name = value[4:]
        resolved = os.getenv(env_name)
        if resolved in (None, ""):
            raise ValueError(f"Environment variable not set: {env_name}")
        return resolved
    return value


def _load_provider_configs(
    provider_entries: Iterable[dict[str, Any]], *, resolve_secrets: bool
) -> list[ProviderConfig]:
    _resolve = _resolve_env_ref if resolve_secrets else (lambda v: v)
    providers: list[ProviderConfig] = []
    for provider_data in provider_entries:
        raw_model_list = cast(
            Iterable[Any],
            provider_data.get("model_list", provider_data.get("models", [])),
        )
        normalized_model_list: list[str | dict[str, Any]] = []
        model_stream: dict[str, bool] = {}
        for item in raw_model_list:
            if isinstance(item, str):
                normalized_model_list.append(item)
                model_stream[item] = False
                continue
            if isinstance(item, dict):
                model_name = str(item.get("name", "")).strip()
                if not model_name:
                    continue
                stream = bool(item.get("stream", False))
                normalized_model_list.append({"name": model_name, "stream": stream})
                model_stream[model_name] = stream
                continue
            normalized_name = str(item)
            normalized_model_list.append(normalized_name)
            model_stream[normalized_name] = False

        provider = ProviderConfig.model_validate(
            {
                "name": str(provider_data.get("name", "")),
                "type": str(provider_data.get("type", "")),
                "api_keys": [_resolve(v) for v in list(provider_data.get("api_keys", []))],
                "model_list": normalized_model_list,
                "extra_headers": {
                    str(key): str(value)
                    for key, value in cast(
                        dict[Any, Any], provider_data.get("extra_headers", {})
                    ).items()
                },
                "base_url": provider_data.get("base_url"),
                "endpoint": provider_data.get("endpoint"),
                "api_version": provider_data.get("api_version"),
                "deployment": provider_data.get("deployment"),
                "anthropic_version": provider_data.get("anthropic_version"),
                "project_id": provider_data.get("project_id"),
                "location": provider_data.get("location"),
                "credentials_path": provider_data.get("credentials_path"),
            }
        )
        # model_post_init() normalizes direct construction as well; overwrite here so TOML
        # loading and direct instantiation converge on the same final representation.
        provider._model_stream = model_stream
        provider.model_list = list(model_stream.keys())
        providers.append(provider)
    return providers


def resolve_model_ref(
    model_ref: str, providers: Optional[Iterable[ProviderConfig]] = None
) -> ResolvedModel:
    if not model_ref or "/" not in model_ref:
        raise ValueError("Model reference must use provider/model format")

    provider_name, model_name = model_ref.split("/", 1)
    if not provider_name or not model_name:
        raise ValueError("Model reference must use provider/model format")

    if providers is None:
        if provider_name not in _DEFAULT_PROVIDER_NAMES:
            raise ValueError(f"Unknown provider: {provider_name}")
        return ResolvedModel(provider_name=provider_name, model_name=model_name)

    provider_map = {provider.name: provider for provider in providers}
    if provider_name not in provider_map:
        raise ValueError(f"Unknown provider: {provider_name}")

    provider = provider_map[provider_name]
    if provider.model_list and model_name not in provider.model_list:
        raise ValueError(f"Model '{model_name}' is not declared for provider '{provider_name}'")

    credential_source = None
    if provider.api_keys:
        first = provider.api_keys[0]
        if isinstance(first, str):
            credential_source = first
        elif isinstance(first, dict):
            key_value = first.get("key")
            credential_source = str(key_value) if key_value else None

    return ResolvedModel(
        provider_name=provider_name,
        model_name=model_name,
        provider=provider,
        credential_source=credential_source,
        stream=provider._model_stream.get(model_name, False),
    )


def _resolve_runtime_section(
    data: dict[str, Any], overrides: RuntimeOverrides
) -> tuple[str, str, str]:
    runtime = cast(dict[str, Any], data.get("runtime", {}))
    docs_dir = str(overrides.output_dir or runtime.get("output_dir", "docs"))
    output_dir = os.path.join(docs_dir, "temp")
    dependency_graph_dir = os.path.join(output_dir, DEPENDENCY_GRAPHS_DIR)
    return docs_dir, output_dir, dependency_graph_dir


def _resolve_agent_instructions(
    data: dict[str, Any], overrides: RuntimeOverrides
) -> dict[str, Any] | None:
    agent = cast(dict[str, Any], data.get("agent", {}))
    merged = {
        key: value
        for key, value in {
            "include_patterns": agent.get("include_patterns"),
            "exclude_patterns": agent.get("exclude_patterns"),
            "focus_modules": agent.get("focus_modules"),
            "doc_type": agent.get("doc_type"),
            "custom_instructions": agent.get("custom_instructions"),
        }.items()
        if value not in (None, [], "")
    }
    if overrides.agent_instructions is not None:
        merged = {**merged, **overrides.agent_instructions}
    return merged or None


def _resolve_refinement_section(
    data: dict[str, Any],
) -> RefinementConfig:
    refinement = cast(dict[str, Any], data.get("refinement", {}))
    return RefinementConfig.model_validate(refinement)


def _resolve_incremental_section(
    data: dict[str, Any],
) -> IncrementalConfig:
    incremental = cast(dict[str, Any], data.get("incremental", {}))
    return IncrementalConfig.model_validate(incremental)


def _validate_generation_models(
    *,
    main_model: str,
    cluster_model: str,
    fallback_models: list[str],
    long_context_model: str | None,
    providers: list[ProviderConfig],
) -> None:
    resolve_model_ref(main_model, providers)
    resolve_model_ref(cluster_model, providers)
    for ref in fallback_models:
        resolve_model_ref(ref, providers)
    if long_context_model:
        resolve_model_ref(long_context_model, providers)


def _build_codewiki_config(
    *,
    repo_path: str,
    data: dict[str, Any],
    providers: list[ProviderConfig],
    overrides: RuntimeOverrides,
    context: str,
) -> CodeWikiConfig:
    runtime = cast(dict[str, Any], data.get("runtime", {}))
    tokens = cast(dict[str, Any], data.get("tokens", {}))
    generation = cast(dict[str, Any], data.get("generation", {}))
    postprocess = cast(dict[str, Any], data.get("postprocess", {}))
    refinement_config = _resolve_refinement_section(data)
    incremental_config = _resolve_incremental_section(data)
    docs_dir = str(overrides.output_dir or runtime.get("output_dir", "docs"))
    output_dir = os.path.join(docs_dir, "temp")
    dependency_graph_dir = os.path.join(output_dir, DEPENDENCY_GRAPHS_DIR)
    fallback_models = (
        overrides.fallback_models
        if overrides.fallback_models is not None
        else list(cast(list[str], generation.get("fallback_models", [])))
    )
    agent_instructions = overrides.agent_instructions
    if agent_instructions is None:
        agent_instructions = _resolve_agent_instructions(data, RuntimeOverrides()) or None
    postprocess_config = PostprocessConfig.model_validate(postprocess)

    return CodeWikiConfig(
        repo_path=repo_path,
        docs_dir=docs_dir,
        output_dir=output_dir,
        dependency_graph_dir=dependency_graph_dir,
        context=cast(Any, context),
        max_depth=overrides.max_depth
        if overrides.max_depth is not None
        else int(runtime.get("max_depth", MAX_DEPTH)),
        main_model=overrides.main_model
        if overrides.main_model is not None
        else str(generation.get("main_model", "")),
        cluster_model=(
            overrides.cluster_model
            if overrides.cluster_model is not None
            else str(generation.get("cluster_model", ""))
        ),
        fallback_model=[str(item) for item in fallback_models] if fallback_models else ["glm-4p5"],
        long_context_model=(
            overrides.long_context_model
            if overrides.long_context_model is not None
            else cast(Optional[str], generation.get("long_context_model"))
        ),
        long_context_fallback=(
            overrides.long_context_fallback
            if overrides.long_context_fallback is not None
            else list(generation.get("long_context_fallback", []))
        ),
        long_context_threshold=(
            overrides.long_context_threshold
            if overrides.long_context_threshold is not None
            else int(tokens.get("long_context_threshold", DEFAULT_LONG_CONTEXT_THRESHOLD))
        ),
        max_tokens=overrides.max_tokens
        if overrides.max_tokens is not None
        else int(tokens.get("max_tokens", DEFAULT_MAX_TOKENS)),
        max_token_per_module=(
            overrides.max_token_per_module
            if overrides.max_token_per_module is not None
            else int(tokens.get("max_token_per_module", DEFAULT_MAX_TOKEN_PER_MODULE))
        ),
        max_token_per_leaf_module=(
            overrides.max_token_per_leaf_module
            if overrides.max_token_per_leaf_module is not None
            else int(tokens.get("max_token_per_leaf_module", DEFAULT_MAX_TOKEN_PER_LEAF_MODULE))
        ),
        max_input_tokens=int(tokens.get("max_input_tokens", 800_000)),
        long_context_max_input_tokens=int(tokens.get("long_context_max_input_tokens", 800_000)),
        max_concurrent=(
            overrides.max_concurrent
            if overrides.max_concurrent is not None
            else int(runtime.get("max_concurrent", DEFAULT_MAX_CONCURRENT))
        ),
        max_retries=overrides.max_retries
        if overrides.max_retries is not None
        else int(runtime.get("max_retries", DEFAULT_MAX_RETRIES)),
        output_language=(
            overrides.output_language
            if overrides.output_language is not None
            else str(runtime.get("output_language", "en"))
        ),
        postprocess=postprocess_config,
        refinement=refinement_config,
        incremental=incremental_config,
        agent_instructions=agent_instructions,
        providers=providers,
    )


def load_config(
    path: str | Path,
    repo_path: str,
    overrides: RuntimeOverrides | None = None,
    *,
    context: str = "cli",
    resolve_secrets: bool = True,
) -> CodeWikiConfig:
    """Load and validate a TOML config file into CodeWikiConfig."""
    config_path = Path(path)
    data = _read_toml(config_path)
    overrides = overrides or RuntimeOverrides()

    providers = _load_provider_configs(
        cast(Iterable[dict[str, Any]], data.get("providers", [])),
        resolve_secrets=resolve_secrets,
    )

    generation = cast(dict[str, Any], data.get("generation", {}))
    main_model = str(overrides.main_model or generation.get("main_model", ""))
    cluster_model = str(overrides.cluster_model or generation.get("cluster_model", ""))
    fallback_models = list(
        overrides.fallback_models
        if overrides.fallback_models is not None
        else cast(list[str], generation.get("fallback_models", []))
    )
    long_context_model = (
        overrides.long_context_model
        if overrides.long_context_model is not None
        else cast(Optional[str], generation.get("long_context_model"))
    )

    _validate_generation_models(
        main_model=main_model,
        cluster_model=cluster_model,
        fallback_models=[str(item) for item in fallback_models],
        long_context_model=long_context_model,
        providers=providers,
    )

    return _build_codewiki_config(
        repo_path=repo_path,
        data=data,
        providers=providers,
        overrides=overrides,
        context=context,
    )
