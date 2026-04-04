"""Unified runtime configuration model for CodeWiki."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, PrivateAttr


class ProviderConfig(BaseModel):
    name: str
    type: str = "openai_compatible"
    api_keys: list[Any] = Field(default_factory=list)
    model_list: list[str | dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("model_list", "models"),
        serialization_alias="model_list",
    )
    extra_headers: dict[str, str] = Field(default_factory=dict)
    base_url: str | None = None
    endpoint: str | None = None
    api_version: str | None = None
    deployment: str | None = None
    anthropic_version: str | None = None
    project_id: str | None = None
    location: str | None = None
    credentials_path: str | None = None
    _model_stream: dict[str, bool] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        # Direct construction in tests and local helpers may bypass config_loader,
        # so normalize model_list here as a fallback. The loader still performs
        # the same normalization when reading TOML so downstream code only relies
        # on provider.model_list + provider._model_stream, never raw dict items.
        normalized_model_list: list[str | dict[str, Any]] = []
        model_stream: dict[str, bool] = {}
        for item in self.model_list:
            if isinstance(item, str):
                model_name = item
                stream = False
            elif isinstance(item, dict):
                model_name = str(item.get("name", "")).strip()
                if not model_name:
                    continue
                stream = bool(item.get("stream", False))
            else:
                model_name = str(item)
                stream = False
            normalized_model_list.append(model_name)
            model_stream[model_name] = stream
        self.model_list = normalized_model_list
        self._model_stream = model_stream


class PostprocessConfig(BaseModel):
    strict: bool = False
    fix_links: bool = True
    degrade_mermaid: bool = False
    repair_model: str = ""
    repair_fallback_1: str = ""
    repair_fallback_2: str = ""
    repair_batch_size: int = 8
    repair_max_retries: int = 2


class CodeWikiConfig(BaseModel):
    """Canonical config shared by CLI, backend, and web entry points."""

    repo_path: str
    docs_dir: str
    output_dir: str = ""
    dependency_graph_dir: str = ""
    context: Literal["cli", "web"] = "cli"
    max_depth: int = 2

    llm_base_url: str = ""
    llm_api_key: str = ""
    main_model: str = ""
    cluster_model: str = ""
    fallback_model: str = "glm-4p5"
    long_context_model: str | None = None
    long_context_threshold: int = 200_000

    max_tokens: int = 32_768
    max_token_per_module: int = 36_369
    max_token_per_leaf_module: int = 16_000

    max_concurrent: int = 3
    max_retries: int = 2

    output_language: str = "en"
    postprocess: PostprocessConfig = Field(default_factory=PostprocessConfig)

    agent_instructions: dict[str, Any] | None = None
    providers: list[ProviderConfig] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @property
    def include_patterns(self) -> list[str] | None:
        if self.agent_instructions:
            return self.agent_instructions.get("include_patterns")
        return None

    @property
    def exclude_patterns(self) -> list[str] | None:
        if self.agent_instructions:
            return self.agent_instructions.get("exclude_patterns")
        return None

    @property
    def focus_modules(self) -> list[str] | None:
        if self.agent_instructions:
            return self.agent_instructions.get("focus_modules")
        return None

    @property
    def doc_type(self) -> str | None:
        if self.agent_instructions:
            return self.agent_instructions.get("doc_type")
        return None

    @property
    def custom_instructions(self) -> str | None:
        if self.agent_instructions:
            return self.agent_instructions.get("custom_instructions")
        return None

    def get_prompt_addition(self) -> str:
        if not self.agent_instructions:
            return ""

        additions: list[str] = []
        if self.doc_type:
            doc_type_instructions = {
                "api": "Focus on API documentation: endpoints, parameters, return types, and usage examples.",
                "architecture": "Focus on architecture documentation: system design, component relationships, and data flow.",
                "user-guide": "Focus on user guide documentation: how to use features, step-by-step tutorials.",
                "developer": "Focus on developer documentation: code structure, contribution guidelines, and implementation details.",
            }
            additions.append(
                doc_type_instructions.get(
                    self.doc_type.lower(),
                    f"Focus on generating {self.doc_type} documentation.",
                )
            )

        if self.focus_modules:
            additions.append(
                "Pay special attention to and provide more detailed documentation "
                f"for these modules: {', '.join(self.focus_modules)}"
            )

        if self.custom_instructions:
            additions.append(f"Additional instructions: {self.custom_instructions}")

        return "\n".join(additions) if additions else ""
