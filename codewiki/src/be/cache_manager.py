"""Unified cache system for CodeWiki."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CACHE_REGISTRY_FILENAME = "cache_registry.json"
_SCHEMA_VERSION = "cache.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def module_artifact_id(doc_id: str) -> str:
    return doc_id if doc_id.startswith("module:") else f"module:{doc_id}"


def overview_artifact_id(doc_id: str) -> str:
    if doc_id == "root":
        return "overview:root"
    return doc_id if doc_id.startswith("overview:") else f"overview:{doc_id}"


@dataclass
class CacheEntry:
    """Metadata for a single cached artifact."""

    artifact_id: str
    input_hash: str = ""
    status: str = "missing"  # valid | stale | missing | running | failed
    depends_on: list[str] = field(default_factory=list)
    output_path: str = ""
    output_file: str = ""
    model: str = ""
    attempt_count: int = 0
    error: str = ""
    updated_at: str = ""


class CacheManager:
    """Unified cache with dependency cascade and background persistence."""

    OVERVIEW_REGENERATE_THRESHOLD = 0.5

    def __init__(self, cache_dir: str, flush_interval: float = 10.0):
        self._cache_dir = cache_dir
        self._flush_interval = flush_interval
        self._entries: dict[str, CacheEntry] = {}
        self._reverse_deps: dict[str, set[str]] = {}
        self._metadata: dict[str, str] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._stopped = False
        self._wake_event = threading.Event()
        self._flush_thread: threading.Thread | None = None
        self._load()

    def is_valid(self, artifact_id: str, current_input_hash: str) -> bool:
        """Check if artifact is cached and still valid."""
        with self._lock:
            entry = self._entries.get(artifact_id)
            if entry is None:
                return False
            return entry.status == "valid" and entry.input_hash == current_input_hash

    def get_entry(self, artifact_id: str) -> CacheEntry | None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            return copy.copy(entry) if entry else None

    def get_input_hash(self, artifact_id: str) -> str | None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            return entry.input_hash if entry else None

    def get_output_file(self, artifact_id: str) -> str | None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            return entry.output_file if entry else None

    def output_file_assignments(self) -> dict[str, str]:
        """Return a snapshot of {output_file: artifact_id} for all current entries.

        Used by callers (e.g. _initialize_cache_from_tree) to detect collisions
        against entries that already exist in the registry, not just within a
        single batch of new tasks.
        """
        with self._lock:
            return {
                entry.output_file: artifact_id
                for artifact_id, entry in self._entries.items()
                if entry.output_file
            }

    def get_metadata(self) -> dict[str, str]:
        with self._lock:
            return dict(self._metadata)

    def update_metadata(self, **metadata: str) -> None:
        with self._lock:
            self._metadata.update(
                {key: value for key, value in metadata.items() if value is not None}
            )
            self._dirty = True

    def _set_depends_on_locked(self, artifact_id: str, depends_on: list[str]) -> None:
        entry = self._entries[artifact_id]
        for dep in entry.depends_on:
            dependents = self._reverse_deps.get(dep)
            if dependents is not None:
                dependents.discard(artifact_id)
                if not dependents:
                    self._reverse_deps.pop(dep, None)
        entry.depends_on = list(depends_on)
        for dep in entry.depends_on:
            self._reverse_deps.setdefault(dep, set()).add(artifact_id)

    def plan_task(
        self,
        artifact_id: str,
        output_file: str,
        depends_on: list[str] | None = None,
    ) -> None:
        """Register a task before execution."""
        with self._lock:
            for other_id, other in self._entries.items():
                if output_file and other_id != artifact_id and other.output_file == output_file:
                    raise ValueError(
                        f"Output file collision: {output_file!r} already assigned to {other_id!r}"
                    )

            existing = self._entries.get(artifact_id)
            if existing and existing.status == "valid":
                if depends_on is not None:
                    self._set_depends_on_locked(artifact_id, depends_on)
                existing.output_file = output_file
                existing.updated_at = _now()
                self._dirty = True
                return

            self._entries[artifact_id] = CacheEntry(
                artifact_id=artifact_id,
                status="missing",
                output_file=output_file,
                updated_at=_now(),
            )
            self._set_depends_on_locked(artifact_id, depends_on or [])
            self._dirty = True

    def mark_running(self, artifact_id: str) -> None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            if entry is None:
                entry = CacheEntry(artifact_id=artifact_id)
                self._entries[artifact_id] = entry
            entry.status = "running"
            entry.updated_at = _now()
            self._dirty = True

    def mark_done(
        self,
        artifact_id: str,
        input_hash: str,
        output_path: str,
        model: str = "",
        output_file: str = "",
        depends_on: list[str] | None = None,
    ) -> None:
        """Mark artifact as successfully generated."""
        with self._lock:
            entry = self._entries.get(artifact_id)
            if entry is None:
                entry = CacheEntry(artifact_id=artifact_id)
                self._entries[artifact_id] = entry
            entry.input_hash = input_hash
            entry.status = "valid"
            entry.output_path = output_path
            entry.model = model
            entry.error = ""
            entry.attempt_count += 1
            entry.updated_at = _now()
            if output_file:
                entry.output_file = output_file
            if depends_on is not None:
                self._set_depends_on_locked(artifact_id, depends_on)
            self._dirty = True

    def mark_failed(self, artifact_id: str, error: str) -> None:
        with self._lock:
            entry = self._entries.get(artifact_id)
            if entry is None:
                entry = CacheEntry(artifact_id=artifact_id)
                self._entries[artifact_id] = entry
            entry.status = "failed"
            entry.error = error
            entry.attempt_count += 1
            entry.updated_at = _now()
            self._dirty = True

    def invalidate(self, artifact_id: str) -> None:
        """Mark stale and recursively cascade to all downstream dependents."""
        with self._lock:
            self._invalidate_locked(artifact_id)
            self._dirty = True

    def invalidate_downstream(self, artifact_ids: list[str]) -> None:
        """Invalidate all entries that transitively depend on any of the given IDs."""
        with self._lock:
            for artifact_id in artifact_ids:
                self._invalidate_locked(artifact_id)
            self._dirty = True

    def _invalidate_locked(self, artifact_id: str) -> None:
        queue: deque[str] = deque([artifact_id])
        seen: set[str] = set()
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            entry = self._entries.get(current)
            if entry and entry.status not in ("stale", "missing"):
                entry.status = "stale"
                entry.updated_at = _now()
            for dependent in self._reverse_deps.get(current, ()):
                if dependent not in seen:
                    queue.append(dependent)

    def get_stale_entries(self, prefix: str = "") -> list[CacheEntry]:
        with self._lock:
            return [
                copy.copy(entry)
                for entry in self._entries.values()
                if entry.status in ("stale", "missing", "failed")
                and (not prefix or entry.artifact_id.startswith(prefix))
            ]

    def flush(self) -> None:
        """Write to disk immediately. Safe to call from any thread."""
        with self._lock:
            if not self._dirty:
                return
            self._write_locked()
            self._dirty = False

    def start(self) -> None:
        """Start background flush thread."""
        if self._flush_thread is not None:
            return
        self._stopped = False
        self._wake_event.clear()
        self._flush_thread = threading.Thread(
            target=self._periodic_flush,
            daemon=True,
            name="cache-flush",
        )
        self._flush_thread.start()

    def stop(self) -> None:
        """Stop background flush thread and do a final flush."""
        self._stopped = True
        self._wake_event.set()
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=5.0)
            self._flush_thread = None
        self.flush()

    def _periodic_flush(self) -> None:
        while not self._stopped:
            self._wake_event.wait(self._flush_interval)
            self._wake_event.clear()
            if self._stopped:
                break
            try:
                self.flush()
            except Exception as exc:
                logger.warning("Cache flush failed: %s", exc)

    def _registry_path(self) -> str:
        return os.path.join(self._cache_dir, CACHE_REGISTRY_FILENAME)

    def _load(self) -> None:
        path = self._registry_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("schema_version") != _SCHEMA_VERSION:
                logger.warning("Cache registry schema mismatch — starting fresh")
                return
            self._metadata = {
                key: value
                for key, value in data.get("metadata", {}).items()
                if isinstance(key, str) and isinstance(value, str)
            }
            for artifact_id, raw in data.get("entries", {}).items():
                entry = CacheEntry(
                    artifact_id=artifact_id,
                    input_hash=raw.get("input_hash", ""),
                    status=raw.get("status", "missing"),
                    depends_on=raw.get("depends_on", []),
                    output_path=raw.get("output_path", ""),
                    output_file=raw.get("output_file", ""),
                    model=raw.get("model", ""),
                    attempt_count=raw.get("attempt_count", 0),
                    error=raw.get("error", ""),
                    updated_at=raw.get("updated_at", ""),
                )
                if entry.status == "running":
                    entry.status = "stale"
                    logger.info(
                        "Cache entry '%s' was running at shutdown — reset to stale",
                        artifact_id,
                    )
                self._entries[artifact_id] = entry
                self._set_depends_on_locked(artifact_id, raw.get("depends_on", []))
        except Exception as exc:
            logger.warning("Failed to load cache registry: %s — starting fresh", exc)

    def _write_locked(self) -> None:
        data: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "metadata": dict(self._metadata),
            "entries": {
                artifact_id: {
                    key: value for key, value in asdict(entry).items() if key != "artifact_id"
                }
                for artifact_id, entry in self._entries.items()
            },
        }
        path = self._registry_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise
