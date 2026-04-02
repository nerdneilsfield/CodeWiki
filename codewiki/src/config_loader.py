from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, cast
import os
import tomllib

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
class ProviderConfig:
    name: str
    type: str
    api_keys: list[Any] = field(default_factory=list)
    model_list: list[str] = field(default_factory=list)
    extra_headers: Dict[str, str] = field(default_factory=dict)
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    api_version: Optional[str] = None
    deployment: Optional[str] = None
    anthropic_version: Optional[str] = None
    project_id: Optional[str] = None
    location: Optional[str] = None
    credentials_path: Optional[str] = None


@dataclass
class RuntimeSection:
    output_dir: str = "docs"
    max_depth: int = MAX_DEPTH
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    max_retries: int = DEFAULT_MAX_RETRIES
    output_language: str = "en"
    postprocess_strict: bool = False


@dataclass
class TokensSection:
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_token_per_module: int = DEFAULT_MAX_TOKEN_PER_MODULE
    max_token_per_leaf_module: int = DEFAULT_MAX_TOKEN_PER_LEAF_MODULE
    long_context_threshold: int = DEFAULT_LONG_CONTEXT_THRESHOLD


@dataclass
class GenerationSection:
    main_model: str
    cluster_model: str
    fallback_models: list[str] = field(default_factory=list)
    long_context_model: Optional[str] = None


@dataclass
class AgentSection:
    include_patterns: Optional[list[str]] = None
    exclude_patterns: Optional[list[str]] = None
    focus_modules: Optional[list[str]] = None
    doc_type: Optional[str] = None
    custom_instructions: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in {
                "include_patterns": self.include_patterns,
                "exclude_patterns": self.exclude_patterns,
                "focus_modules": self.focus_modules,
                "doc_type": self.doc_type,
                "custom_instructions": self.custom_instructions,
            }.items()
            if value not in (None, [], "")
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
    postprocess_strict: Optional[bool] = None
    main_model: Optional[str] = None
    cluster_model: Optional[str] = None
    fallback_models: Optional[list[str]] = None
    long_context_model: Optional[str] = None
    long_context_threshold: Optional[int] = None
    agent_instructions: Optional[Dict[str, Any]] = None


@dataclass
class ResolvedModel:
    provider_name: str
    model_name: str
    provider: Optional[ProviderConfig] = None
    credential_source: Optional[str] = None


@dataclass
class AppConfig:
    runtime: RuntimeSection
    tokens: TokensSection
    generation: GenerationSection
    agent: AgentSection
    providers: list[ProviderConfig]

    def get_provider(self, name: str) -> ProviderConfig:
        for provider in self.providers:
            if provider.name == name:
                return provider
        raise ValueError(f"Unknown provider: {name}")

    def resolve_model_ref(self, model_ref: str) -> ResolvedModel:
        return resolve_model_ref(model_ref, self.providers)

    def to_runtime_config(self, repo_path: str, overrides: RuntimeOverrides | None = None):
        from codewiki.src.config import Config

        overrides = overrides or RuntimeOverrides()
        runtime = self.runtime
        tokens = self.tokens
        generation = self.generation

        output_dir = (
            overrides.output_dir if overrides.output_dir is not None else runtime.output_dir
        )
        base_output_dir = os.path.join(output_dir, "temp")
        docs_dir = output_dir
        dependency_graph_dir = os.path.join(base_output_dir, DEPENDENCY_GRAPHS_DIR)
        output_root = base_output_dir

        main_model = (
            overrides.main_model if overrides.main_model is not None else generation.main_model
        )
        cluster_model = (
            overrides.cluster_model
            if overrides.cluster_model is not None
            else generation.cluster_model
        )
        fallback_models = (
            overrides.fallback_models
            if overrides.fallback_models is not None
            else generation.fallback_models
        )
        long_context_model = (
            overrides.long_context_model
            if overrides.long_context_model is not None
            else generation.long_context_model
        )

        return Config(
            repo_path=repo_path,
            output_dir=output_root,
            dependency_graph_dir=dependency_graph_dir,
            docs_dir=docs_dir,
            max_depth=(
                overrides.max_depth if overrides.max_depth is not None else runtime.max_depth
            ),
            main_model=main_model,
            cluster_model=cluster_model,
            fallback_model=",".join(fallback_models),
            long_context_model=long_context_model,
            long_context_threshold=(
                overrides.long_context_threshold
                if overrides.long_context_threshold is not None
                else tokens.long_context_threshold
            ),
            max_tokens=(
                overrides.max_tokens if overrides.max_tokens is not None else tokens.max_tokens
            ),
            max_token_per_module=(
                overrides.max_token_per_module
                if overrides.max_token_per_module is not None
                else tokens.max_token_per_module
            ),
            max_token_per_leaf_module=(
                overrides.max_token_per_leaf_module
                if overrides.max_token_per_leaf_module is not None
                else tokens.max_token_per_leaf_module
            ),
            max_concurrent=(
                overrides.max_concurrent
                if overrides.max_concurrent is not None
                else runtime.max_concurrent
            ),
            max_retries=(
                overrides.max_retries if overrides.max_retries is not None else runtime.max_retries
            ),
            output_language=(
                overrides.output_language
                if overrides.output_language is not None
                else runtime.output_language
            ),
            postprocess_strict=(
                overrides.postprocess_strict
                if overrides.postprocess_strict is not None
                else runtime.postprocess_strict
            ),
            agent_instructions=(
                overrides.agent_instructions
                if overrides.agent_instructions is not None
                else (self.agent.to_dict() or None)
            ),
            providers=self.providers,
        )


def _read_toml(path: Path) -> Dict[str, Any]:
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
    )


def load_app_config(path: str | Path, resolve_secrets: bool = True) -> AppConfig:
    """Load and validate a TOML config file.

    Args:
        path: Path to the TOML file.
        resolve_secrets: When True (default), ``env:VAR`` references in
            ``api_keys`` are resolved at load time and an error is raised for
            missing variables.  Pass ``False`` for read-only operations such as
            ``config show`` and ``config validate`` that must work without the
            secrets being present in the environment.
    """
    config_path = Path(path)
    data = _read_toml(config_path)

    runtime = RuntimeSection(**data.get("runtime", {}))
    tokens = TokensSection(**data.get("tokens", {}))
    generation = GenerationSection(**data.get("generation", {}))
    agent = AgentSection(**data.get("agent", {}))

    _resolve = _resolve_env_ref if resolve_secrets else (lambda v: v)

    providers = []
    for provider_data in data.get("providers", []):
        provider_dict = cast(dict[str, Any], provider_data)
        api_keys = list(provider_dict.get("api_keys", []))
        model_list = [
            str(model) for model in cast(Iterable[Any], provider_dict.get("model_list", []))
        ]
        extra_headers = {
            str(key): str(value)
            for key, value in cast(dict[Any, Any], provider_dict.get("extra_headers", {})).items()
        }
        providers.append(
            ProviderConfig(
                name=str(provider_dict.get("name", "")),
                type=str(provider_dict.get("type", "")),
                api_keys=[_resolve(v) for v in api_keys],
                model_list=model_list,
                extra_headers=extra_headers,
                base_url=cast(Optional[str], provider_dict.get("base_url")),
                endpoint=cast(Optional[str], provider_dict.get("endpoint")),
                api_version=cast(Optional[str], provider_dict.get("api_version")),
                deployment=cast(Optional[str], provider_dict.get("deployment")),
                anthropic_version=cast(Optional[str], provider_dict.get("anthropic_version")),
                project_id=cast(Optional[str], provider_dict.get("project_id")),
                location=cast(Optional[str], provider_dict.get("location")),
                credentials_path=cast(Optional[str], provider_dict.get("credentials_path")),
            )
        )

    app_config = AppConfig(
        runtime=runtime,
        tokens=tokens,
        generation=generation,
        agent=agent,
        providers=providers,
    )

    app_config.resolve_model_ref(app_config.generation.main_model)
    app_config.resolve_model_ref(app_config.generation.cluster_model)
    for ref in app_config.generation.fallback_models:
        app_config.resolve_model_ref(ref)
    if app_config.generation.long_context_model:
        app_config.resolve_model_ref(app_config.generation.long_context_model)

    return app_config
