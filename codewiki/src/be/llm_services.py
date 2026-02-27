"""
LLM service factory for creating configured LLM clients.
"""
import time
import logging
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.openai import OpenAIModelSettings
from pydantic_ai.models.fallback import FallbackModel
from openai import OpenAI, AsyncOpenAI
import httpx

from codewiki.src.config import Config

_logger = logging.getLogger(__name__)

# Delays (seconds) between successive retries: 10 s, 30 s, 90 s
_RETRY_DELAYS = [10, 30, 90]

# Long-running LLM calls can take well over the default 5 s httpx timeout.
_LLM_TIMEOUT = httpx.Timeout(180.0)


def _make_provider(config: Config) -> OpenAIProvider:
    """Create an OpenAIProvider with a 180 s timeout."""
    return OpenAIProvider(
        openai_client=AsyncOpenAI(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            timeout=_LLM_TIMEOUT,
        )
    )


def create_main_model(config: Config) -> OpenAIModel:
    """Create the main LLM model from configuration."""
    return OpenAIModel(
        model_name=config.main_model,
        provider=_make_provider(config),
        settings=OpenAIModelSettings(
            temperature=0.0,
            max_tokens=config.max_tokens
        )
    )


def create_fallback_models(config: Config) -> FallbackModel:
    """Create fallback models chain from configuration.

    ``config.fallback_model`` may contain a single model name or multiple
    names separated by commas (e.g. ``"glm-4p5,gpt-4o-mini"``).  Each name
    becomes an additional fallback in the chain after the main model.
    """
    provider = _make_provider(config)
    settings = OpenAIModelSettings(temperature=0.0, max_tokens=config.max_tokens)

    main = create_main_model(config)

    fallback_names = [n.strip() for n in config.fallback_model.split(",") if n.strip()]
    fallbacks = [
        OpenAIModel(model_name=name, provider=provider, settings=settings)
        for name in fallback_names
    ]

    return FallbackModel(main, *fallbacks)


def create_openai_client(config: Config) -> OpenAI:
    """Create OpenAI client from configuration."""
    return OpenAI(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        timeout=_LLM_TIMEOUT,
    )


def call_llm(
    prompt: str,
    config: Config,
    model: str = None,
    temperature: float = 0.0
) -> str:
    """
    Call LLM with the given prompt.

    Automatically switches to ``config.long_context_model`` when the prompt
    token count exceeds ``config.max_token_per_module`` and a long-context
    model is configured.

    Args:
        prompt: The prompt to send
        config: Configuration containing LLM settings
        model: Model name (defaults to config.main_model)
        temperature: Temperature setting

    Returns:
        LLM response text
    """
    from codewiki.src.be.utils import count_tokens

    if model is None:
        model = config.main_model

    # Auto-switch to long-context model when prompt is too large
    prompt_tokens = count_tokens(prompt)
    if (
        config.long_context_model
        and prompt_tokens > config.long_context_threshold
        and model == config.main_model
    ):
        import logging
        logging.getLogger(__name__).info(
            f"Prompt has {prompt_tokens} tokens (> {config.long_context_threshold}), "
            f"switching from {model} to long-context model {config.long_context_model}"
        )
        model = config.long_context_model

    client = create_openai_client(config)
    last_exc: Exception = None
    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            _logger.warning(
                f"call_llm: retrying in {delay}s (attempt {attempt}/{len(_RETRY_DELAYS)}) "
                f"after: {last_exc}"
            )
            time.sleep(delay)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=config.max_tokens
            )
            return response.choices[0].message.content
        except Exception as exc:
            last_exc = exc
    raise last_exc