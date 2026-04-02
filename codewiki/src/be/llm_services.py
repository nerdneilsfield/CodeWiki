"""
LLM service factory for creating configured LLM clients.
"""

import sys
import time
import logging
import random
from functools import lru_cache

import httpx
from anthropic import Anthropic
from openai import OpenAI, AsyncOpenAI
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIModel, OpenAIModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from codewiki.src.config import Config
from codewiki.src.config_loader import resolve_model_ref

_logger = logging.getLogger(__name__)

# Delays (seconds) between successive retries: 10 s, 30 s, 90 s
_RETRY_DELAYS = [10, 30, 90]

# Long-running LLM calls can take well over the default 5 s httpx timeout.
_LLM_TIMEOUT = httpx.Timeout(180.0)


@lru_cache(maxsize=8)
def _get_cached_async_client(base_url: str, api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=_LLM_TIMEOUT)


@lru_cache(maxsize=8)
def _get_cached_async_provider(base_url: str, api_key: str) -> OpenAIProvider:
    return OpenAIProvider(openai_client=_get_cached_async_client(base_url, api_key))


@lru_cache(maxsize=8)
def _get_cached_openai_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key, timeout=_LLM_TIMEOUT)


@lru_cache(maxsize=8)
def _get_cached_anthropic_client(api_key: str, base_url: str | None = None) -> Anthropic:
    return Anthropic(api_key=api_key, base_url=base_url, timeout=_LLM_TIMEOUT)


@lru_cache(maxsize=8)
def _get_cached_anthropic_provider(api_key: str, base_url: str | None = None) -> AnthropicProvider:
    return AnthropicProvider(api_key=api_key, base_url=base_url)


def _model_settings(config: Config) -> OpenAIModelSettings:
    return OpenAIModelSettings(temperature=0.0, max_tokens=config.max_tokens)


def _has_provider_registry(config: Config) -> bool:
    providers = getattr(config, "providers", None)
    return isinstance(providers, list) and len(providers) > 0


def _get_provider_config(config: Config, model_ref: str):
    if not _has_provider_registry(config):
        raise ValueError("Provider registry is not available on runtime config")
    resolved = resolve_model_ref(model_ref, config.providers)
    if resolved.provider is None:
        raise ValueError(f"Provider config not found for model ref: {model_ref}")
    return resolved.provider, resolved.model_name


def _get_provider_api_key(provider_config) -> str:
    if not getattr(provider_config, "api_keys", None):
        return ""
    first = provider_config.api_keys[0]
    if isinstance(first, str):
        return first
    if isinstance(first, dict):
        return str(first.get("key", ""))
    return str(first)


def _make_provider(config: Config) -> OpenAIProvider:
    return _get_cached_async_provider(config.llm_base_url, config.llm_api_key)


def _make_provider_for_model(config: Config, model_ref: str):
    if not _has_provider_registry(config):
        return _make_provider(config)

    provider_config, _ = _get_provider_config(config, model_ref)
    provider_type = provider_config.type
    api_key = _get_provider_api_key(provider_config)

    if provider_type in {"openai_compatible", "azure_openai"}:
        base_url = provider_config.base_url or provider_config.endpoint or config.llm_base_url
        return _get_cached_async_provider(base_url or "", api_key)

    if provider_type == "claude":
        return _get_cached_anthropic_provider(api_key, provider_config.base_url)

    raise ValueError(f"unsupported provider type: {provider_type}")


def create_model_from_ref(config: Config, model_ref: str):
    provider = _make_provider_for_model(config, model_ref)
    provider_config, model_name = _get_provider_config(config, model_ref)
    settings = _model_settings(config)

    if provider_config.type in {"openai_compatible", "azure_openai"}:
        return OpenAIModel(model_name=model_name, provider=provider, settings=settings)

    if provider_config.type == "claude":
        return AnthropicModel(model_name=model_name, provider=provider, settings=settings)

    raise ValueError(f"unsupported provider type: {provider_config.type}")


def create_main_model(config: Config):
    if _has_provider_registry(config):
        return create_model_from_ref(config, config.main_model)
    return OpenAIModel(
        model_name=config.main_model,
        provider=_make_provider(config),
        settings=_model_settings(config),
    )


def create_fallback_models(config: Config) -> FallbackModel:
    main = create_main_model(config)

    fallback_names = [n.strip() for n in config.fallback_model.split(",") if n.strip()]
    fallbacks = []
    for name in fallback_names:
        if _has_provider_registry(config):
            fallbacks.append(create_model_from_ref(config, name))
        else:
            fallbacks.append(
                OpenAIModel(
                    model_name=name,
                    provider=_make_provider(config),
                    settings=_model_settings(config),
                )
            )

    if config.long_context_model:
        if _has_provider_registry(config):
            fallbacks.append(create_model_from_ref(config, config.long_context_model))
        else:
            fallbacks.append(
                OpenAIModel(
                    model_name=config.long_context_model,
                    provider=_make_provider(config),
                    settings=_model_settings(config),
                )
            )

    return FallbackModel(main, *fallbacks)


def create_long_context_model(config: Config):
    if _has_provider_registry(config):
        return create_model_from_ref(config, config.long_context_model)
    return OpenAIModel(
        model_name=config.long_context_model,
        provider=_make_provider(config),
        settings=_model_settings(config),
    )


def select_agent_model(config: Config, estimated_tokens: int = 0):
    if config.long_context_model and estimated_tokens > config.long_context_threshold:
        _logger.info(
            f"Pre-selecting long-context model {config.long_context_model} "
            f"(estimated {estimated_tokens} tokens > threshold {config.long_context_threshold})"
        )
        return create_long_context_model(config)
    return create_fallback_models(config)


def create_openai_client(config: Config) -> OpenAI:
    return _get_cached_openai_client(config.llm_base_url, config.llm_api_key)


def _create_client_for_model(config: Config, model: str):
    if not _has_provider_registry(config):
        return create_openai_client(config), "openai_compatible"

    provider_config, _ = _get_provider_config(config, model)
    provider_type = provider_config.type
    api_key = _get_provider_api_key(provider_config)

    if provider_type in {"openai_compatible", "azure_openai"}:
        base_url = provider_config.base_url or provider_config.endpoint or config.llm_base_url
        return _get_cached_openai_client(base_url or "", api_key), provider_type

    if provider_type == "claude":
        return _get_cached_anthropic_client(api_key, provider_config.base_url), provider_type

    raise ValueError(f"unsupported provider type: {provider_type}")


def _is_cf_timeout(exc: Exception) -> bool:
    msg = str(exc)
    return (
        "524" in msg
        or "A timeout occurred" in msg
        or "cloudflare" in msg.lower()
        or "stream disconnected" in msg.lower()
        or "stream closed before" in msg.lower()
    )


_MAX_RETRY_AFTER = 120.0


def _parse_retry_after(exc: Exception) -> float | None:
    import openai

    if not isinstance(exc, openai.RateLimitError):
        return None
    headers = getattr(getattr(exc, "response", None), "headers", {})
    val = headers.get("retry-after") or headers.get("Retry-After")
    if val:
        try:
            seconds = float(val)
        except (ValueError, OverflowError):
            return None
        if not (0 <= seconds < float("inf")):
            return None
        return min(seconds, _MAX_RETRY_AFTER)
    return None


def _sleep_with_jitter(base_delay: float) -> None:
    actual = base_delay + random.uniform(0, base_delay * 0.5)
    time.sleep(actual)


def _call_llm_streaming(
    client: OpenAI, model: str, prompt: str, temperature: float, config: Config
) -> str:
    chunks: list[str] = []
    with client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=config.max_tokens,
        stream=True,
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                chunks.append(delta)
    return "".join(chunks)


def _call_claude(
    client: Anthropic, model: str, prompt: str, temperature: float, config: Config
) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=config.max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


def call_llm(prompt: str, config: Config, model: str = None, temperature: float = 0.0) -> str:
    from codewiki.src.be.utils import count_tokens

    if model is None:
        model = config.main_model

    prompt_tokens = count_tokens(prompt)
    if (
        config.long_context_model
        and prompt_tokens > config.long_context_threshold
        and model == config.main_model
    ):
        _logger.info(
            f"Switching model: {model} → {config.long_context_model} "
            f"(prompt {prompt_tokens} tokens > threshold {config.long_context_threshold})"
        )
        model = config.long_context_model

    _logger.debug(
        f"call_llm: model={model}, prompt_tokens={prompt_tokens}, temperature={temperature}"
    )

    client, provider_type = _create_client_for_model(config, model)
    if _has_provider_registry(config):
        _, resolved_model_name = _get_provider_config(config, model)
    else:
        resolved_model_name = model

    last_exc: Exception = None
    use_streaming = False
    t0 = time.time()
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            retry_after = _parse_retry_after(last_exc)
            wait = retry_after if retry_after is not None else delay
            msg = (
                f"⚠  LLM retry {attempt}/{len(_RETRY_DELAYS)}"
                f" — model={model}"
                f" — waiting {wait}s"
                f" — reason: {last_exc}" + (" [streaming]" if use_streaming else "")
            )
            _logger.warning(msg)
            try:
                from tqdm import tqdm as _tqdm

                _tqdm.write(msg, file=sys.stderr)
            except Exception:
                print(msg, file=sys.stderr, flush=True)
            if retry_after is not None:
                _logger.info(f"call_llm: honouring Retry-After: {retry_after}s")
                time.sleep(retry_after)
            else:
                _sleep_with_jitter(delay)
            start_msg = f"↻  LLM retry {attempt}/{len(_RETRY_DELAYS)} starting — model={model}"
            _logger.info(start_msg)
            try:
                from tqdm import tqdm as _tqdm

                _tqdm.write(start_msg, file=sys.stderr)
            except Exception:
                print(start_msg, file=sys.stderr, flush=True)
        try:
            if provider_type in {"openai_compatible", "azure_openai"}:
                if use_streaming:
                    content = _call_llm_streaming(
                        client, resolved_model_name, prompt, temperature, config
                    )
                else:
                    response = client.chat.completions.create(
                        model=resolved_model_name,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature,
                        max_tokens=config.max_tokens,
                    )
                    content = response.choices[0].message.content
            elif provider_type == "claude":
                content = _call_claude(client, resolved_model_name, prompt, temperature, config)
            else:
                raise ValueError(f"unsupported provider type: {provider_type}")
            elapsed = time.time() - t0
            _logger.debug(
                f"call_llm [model={model}]: success in {elapsed:.1f}s, "
                f"response length={len(content)}" + (" [streaming]" if use_streaming else "")
            )
            return content
        except Exception as exc:
            last_exc = exc
            if provider_type in {"openai_compatible", "azure_openai"} and _is_cf_timeout(exc):
                use_streaming = True
                _logger.info(
                    f"call_llm [model={model}]: connection timeout detected — switching to streaming for next retry"
                )
    raise last_exc
