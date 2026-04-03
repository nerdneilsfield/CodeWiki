"""Pipeline primitives for documentation generation."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)


@dataclass
class ModuleFailure:
    doc_id: str
    error: str
    retried: bool


@dataclass
class ModuleSkip:
    doc_id: str
    reason: str


@dataclass
class ModuleSummary:
    completed: list[str] = field(default_factory=list)
    failed: list[ModuleFailure] = field(default_factory=list)
    skipped: list[ModuleSkip] = field(default_factory=list)
    retried_then_succeeded: list[str] = field(default_factory=list)
    total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "completed": list(self.completed),
            "failed": [asdict(item) for item in self.failed],
            "skipped": [asdict(item) for item in self.skipped],
            "retried_then_succeeded": list(self.retried_then_succeeded),
        }


@dataclass
class GenerationResult:
    status: Literal["complete", "degraded", "failed"] = "complete"
    warnings: list[str] = field(default_factory=list)
    module_summary: ModuleSummary = field(default_factory=ModuleSummary)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)
        if self.status == "complete":
            self.status = "degraded"

    def mark_failed(self, message: str) -> None:
        self.warnings.append(message)
        self.status = "failed"

    def to_metadata_dict(self) -> dict[str, Any]:
        return {
            "generation_status": self.status,
            "degradation_reasons": list(self.warnings),
            "module_summary": self.module_summary.to_dict(),
        }


@dataclass
class PipelineContext:
    """Mutable context carried between pipeline stages."""

    config: Any
    working_dir: str = ""
    components: dict[str, Any] = field(default_factory=dict)
    leaf_nodes: list[str] = field(default_factory=list)
    module_tree: dict[str, Any] = field(default_factory=dict)
    index_products: Any = None
    gen_state: Any = None
    state_mgr: Any = None
    tree_manager: Any = None
    usage_stats: Any = None
    graph_builder: Any = None
    agent_orchestrator: Any = None
    commit_id: str = ""
    result: GenerationResult = field(default_factory=GenerationResult)


class PipelineStage(Protocol):
    name: str
    failure_policy: Literal["fail_fast", "degraded_ok"]

    async def execute(self, ctx: PipelineContext) -> None: ...


class PipelineRunner:
    """Execute pipeline stages in order and apply failure policy."""

    def __init__(self, stages: list[PipelineStage]):
        self._stages = stages

    async def execute(self, ctx: PipelineContext) -> GenerationResult:
        for stage in self._stages:
            try:
                logger.info("▶ Stage: %s", stage.name)
                await stage.execute(ctx)
                logger.info("✓ Stage: %s complete", stage.name)
            except Exception as exc:
                message = f"{stage.name} failed: {exc}"
                if stage.failure_policy == "fail_fast":
                    logger.error("✗ %s (fail_fast — aborting pipeline)", message)
                    ctx.result.mark_failed(message)
                    break
                logger.warning("⚠ %s (degraded_ok — continuing)", message)
                ctx.result.add_warning(message)
        return ctx.result
