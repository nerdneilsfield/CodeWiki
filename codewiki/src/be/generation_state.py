"""Generation state ledger for documentation generation.

This module holds the single source of truth for doc task status, output file
assignment, dependency tracking, and staleness detection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

GENERATION_STATE_FILENAME = "generation_state.json"
SCHEMA_VERSION = "codewiki.generation_state.v1"

_ACTIONABLE = frozenset({"ready", "failed", "stale"})
_TERMINAL = frozenset({"completed", "skipped"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DocTask:
    """A single documentation generation unit."""

    doc_id: str
    kind: str
    module_path: list[str]
    output_file: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "planned"
    source: str = "manifest"
    parent_doc_id: str = ""
    input_hash: str = ""
    content_hash: str = ""
    prompt_version: str = ""
    language: str = "en"
    model: str = ""
    attempt_count: int = 0
    last_error: str = ""
    updated_at: str = ""

    def mark_running(self) -> None:
        self.status = "running"
        self.updated_at = _utcnow()

    def mark_completed(self, content_hash: str, model: str = "", input_hash: str = "") -> None:
        self.status = "completed"
        self.content_hash = content_hash
        self.model = model
        if input_hash:
            self.input_hash = input_hash
        self.last_error = ""
        self.attempt_count += 1
        self.updated_at = _utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.last_error = error
        self.attempt_count += 1
        self.updated_at = _utcnow()

    def is_stale(self, current_input_hash: str = "") -> bool:
        if self.status != "completed":
            return False
        return bool(current_input_hash and current_input_hash != self.input_hash)


class GenerationState:
    """Task ledger for documentation generation."""

    def __init__(self, repo_commit: str = "", config_fingerprint: str = ""):
        self.schema_version = SCHEMA_VERSION
        self.repo_commit = repo_commit
        self.config_fingerprint = config_fingerprint
        self.tasks: dict[str, DocTask] = {}
        self._output_file_index: dict[str, str] = {}

    def _add_task(self, task: DocTask) -> None:
        existing_owner = self._output_file_index.get(task.output_file)
        if existing_owner and existing_owner != task.doc_id:
            raise ValueError(
                f"output_file {task.output_file!r} already assigned to "
                f"{existing_owner!r}, cannot assign to {task.doc_id!r}"
            )
        self.tasks[task.doc_id] = task
        self._output_file_index[task.output_file] = task.doc_id

    def _register_discovered_task(self, task: DocTask) -> None:
        task.source = "discovered"
        if task.status not in ("planned", "ready"):
            task.status = "planned"
        self._add_task(task)

    def get_task(self, doc_id: str) -> Optional[DocTask]:
        return self.tasks.get(doc_id)

    def get_output_file(self, doc_id: str) -> Optional[str]:
        task = self.tasks.get(doc_id)
        return task.output_file if task else None

    def actionable_task_ids(self) -> list[str]:
        return [tid for tid, t in self.tasks.items() if t.status in _ACTIONABLE]

    def ready_task_ids(self) -> list[str]:
        result: list[str] = []
        for tid, task in self.tasks.items():
            if task.status in ("ready", "failed", "stale"):
                result.append(tid)
                continue
            if task.status != "planned":
                continue
            if all(
                (dep_task := self.tasks.get(dep)) and dep_task.status in _TERMINAL
                for dep in task.depends_on
            ):
                result.append(tid)
        return result

    def _promote_ready(self) -> int:
        promoted = 0
        for task in self.tasks.values():
            if task.status != "planned":
                continue
            if all(
                (dep_task := self.tasks.get(dep)) and dep_task.status in _TERMINAL
                for dep in task.depends_on
            ):
                task.status = "ready"
                task.updated_at = _utcnow()
                promoted += 1
        return promoted

    def _update_task_status(self, doc_id: str, status: str, **kwargs) -> None:
        task = self.tasks.get(doc_id)
        if task is None:
            raise KeyError(f"Unknown doc_id: {doc_id}")
        task.status = status
        for key, value in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, value)
        task.updated_at = _utcnow()

    def _mark_stale_tasks(self, current_input_hashes: dict[str, str]) -> None:
        for doc_id, current_hash in current_input_hashes.items():
            task = self.tasks.get(doc_id)
            if task and task.is_stale(current_hash):
                task.status = "stale"
                task.input_hash = current_hash
                task.updated_at = _utcnow()
                logger.info("Task %s marked stale (input changed)", doc_id)

    def _save(self, path: str) -> None:
        data = {
            "schema_version": self.schema_version,
            "repo_commit": self.repo_commit,
            "config_fingerprint": self.config_fingerprint,
            "tasks": [asdict(task) for task in self.tasks.values()],
        }
        dir_name = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str) -> "GenerationState":
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt generation state at %s (%s) — starting fresh", path, exc)
            return cls()
        state = cls(
            repo_commit=data.get("repo_commit", ""),
            config_fingerprint=data.get("config_fingerprint", ""),
        )
        for raw_task in data.get("tasks", []):
            try:
                task_fields = {
                    key: value
                    for key, value in raw_task.items()
                    if key in DocTask.__dataclass_fields__
                }
                task = DocTask(**task_fields)
            except (TypeError, KeyError) as exc:
                logger.warning("Skipping malformed task record: %s", exc)
                continue
            existing_owner = state._output_file_index.get(task.output_file)
            if existing_owner and existing_owner != task.doc_id:
                logger.warning(
                    "Skipping task %s due to output_file collision with %s while loading generation state",
                    task.doc_id,
                    existing_owner,
                )
                continue
            state.tasks[task.doc_id] = task
            state._output_file_index[task.output_file] = task.doc_id
        return state


class GenerationStateManager:
    """Async-safe wrapper around GenerationState."""

    def __init__(self, state: GenerationState, persist_path: str):
        self._state = state
        self._persist_path = persist_path
        self._lock = asyncio.Lock()

    @property
    def state(self) -> GenerationState:
        return self._state

    async def add_task(self, task: DocTask) -> None:
        async with self._lock:
            self._state._add_task(task)
            self._state._save(self._persist_path)

    async def bulk_add_tasks(self, tasks: list[DocTask]) -> None:
        async with self._lock:
            for task in tasks:
                self._state._add_task(task)
            self._state._save(self._persist_path)

    async def mark_running(self, doc_id: str) -> None:
        async with self._lock:
            self._state._update_task_status(doc_id, "running")
            self._state._save(self._persist_path)

    async def mark_completed(
        self,
        doc_id: str,
        content_hash: str,
        model: str = "",
        input_hash: str = "",
    ) -> None:
        async with self._lock:
            task = self._state.get_task(doc_id)
            if task is None:
                raise KeyError(f"Unknown doc_id: {doc_id}")
            task.mark_completed(content_hash=content_hash, model=model, input_hash=input_hash)
            self._state._save(self._persist_path)

    async def mark_failed(self, doc_id: str, error: str) -> None:
        async with self._lock:
            task = self._state.get_task(doc_id)
            if task is None:
                raise KeyError(f"Unknown doc_id: {doc_id}")
            task.mark_failed(error)
            self._state._save(self._persist_path)

    async def register_discovered_task(self, task: DocTask) -> None:
        async with self._lock:
            self._state._register_discovered_task(task)
            self._state._save(self._persist_path)

    async def promote_ready(self) -> int:
        async with self._lock:
            count = self._state._promote_ready()
            if count:
                self._state._save(self._persist_path)
            return count

    async def mark_stale(self, current_input_hashes: dict[str, str]) -> None:
        async with self._lock:
            self._state._mark_stale_tasks(current_input_hashes)
            self._state._save(self._persist_path)

    async def update_metadata(self, repo_commit: str, config_fingerprint: str) -> None:
        async with self._lock:
            self._state.repo_commit = repo_commit
            self._state.config_fingerprint = config_fingerprint
            self._state._save(self._persist_path)
