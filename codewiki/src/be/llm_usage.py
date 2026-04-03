"""LLM usage tracking data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LLMCallUsage:
    """Token usage from a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    source: str = "api"


@dataclass
class LLMCallResult:
    """Return value from call_llm: content plus optional usage."""

    content: str
    usage: LLMCallUsage | None = None
    model: str = ""


@dataclass
class LLMUsageStats:
    """Accumulated token usage across a generation run."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    def _ensure_model(self, model: str) -> dict[str, int]:
        if model not in self.by_model:
            self.by_model[model] = {"input": 0, "output": 0, "requests": 0}
        return self.by_model[model]

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        requests: int = 1,
        count_towards_totals: bool = True,
    ) -> None:
        model_stats = self._ensure_model(model)
        model_stats["input"] += input_tokens
        model_stats["output"] += output_tokens
        model_stats["requests"] += requests

        if count_towards_totals:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_requests += requests

    def add_totals(self, input_tokens: int, output_tokens: int, requests: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_requests += requests

    def to_dict(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_requests": self.total_requests,
            "by_model": dict(self.by_model),
        }
