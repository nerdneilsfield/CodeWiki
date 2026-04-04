#!/usr/bin/env python3
"""
Background worker for processing documentation generation jobs.
"""

import logging
import os
import queue
import shutil
import time
import threading
import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Dict, Optional

logger = logging.getLogger(__name__)

from codewiki.src.be.documentation_generator import DocumentationGenerator
from codewiki.src.be.cancellation import CancellationToken
from codewiki.src.be.errors import CancellationError
from codewiki.src.config_loader import RuntimeOverrides, load_config
from codewiki.src.logging_setup import configure_web_logging
from .models import JobStatus
from .cache_manager import CacheManager
from .github_processor import GitHubRepoProcessor
from .config import WebAppConfig
from codewiki.src.utils import file_manager


class BackgroundWorker:
    """Background worker for processing documentation generation jobs."""

    def __init__(
        self,
        cache_manager: CacheManager,
        temp_dir: str | None = None,
        config_path: str | None = None,
    ):
        configure_web_logging()
        self.cache_manager = cache_manager
        self.temp_dir = temp_dir or WebAppConfig.TEMP_DIR
        self.config_path = config_path
        self.running = False
        self.processing_queue = Queue(maxsize=WebAppConfig.QUEUE_SIZE)
        self._job_lock = threading.Lock()
        self._cancel_tokens: dict[str, CancellationToken] = {}
        self.job_status: Dict[str, JobStatus] = {}
        self.jobs_file = Path(WebAppConfig.CACHE_DIR) / "jobs.json"
        self.load_job_statuses()

    def start(self):
        """Start the background worker thread."""
        if not self.running:
            self.running = True
            thread = threading.Thread(target=self._worker_loop, daemon=True)
            thread.start()
            logger.info("Background worker started")

    def stop(self):
        """Stop the background worker."""
        self.running = False

    def add_job(self, job_id: str, job: JobStatus) -> bool:
        """Add a job to the processing queue. Returns False if queue is full."""
        self.set_job(job_id, job)
        try:
            self.processing_queue.put(job_id, timeout=5)
            return True
        except queue.Full:
            logger.error("Queue is full, cannot add job %s", job_id)
            with self._job_lock:
                job.status = "failed"
                job.error_message = "Server is at capacity, please try again later"
            return False

    def snapshot_job(self, job_id: str) -> Optional[JobStatus]:
        """Return a thread-safe copy of a single job, or None."""
        with self._job_lock:
            job = self.job_status.get(job_id)
            return copy.copy(job) if job else None

    def snapshot_jobs(self) -> dict[str, JobStatus]:
        """Return a thread-safe copy of all job statuses."""
        with self._job_lock:
            return {key: copy.copy(value) for key, value in self.job_status.items()}

    def set_job(self, job_id: str, job: JobStatus) -> None:
        """Thread-safe write of a job status entry."""
        with self._job_lock:
            self.job_status[job_id] = job

    def delete_job(self, job_id: str) -> None:
        """Thread-safe deletion of a job status entry."""
        with self._job_lock:
            self.job_status.pop(job_id, None)

    def cancel_job(self, job_id: str) -> bool:
        """Request cooperative cancellation for an active job."""
        with self._job_lock:
            token = self._cancel_tokens.get(job_id)
        if token:
            token.cancel()
            return True
        return False

    def load_job_statuses(self):
        """Load job statuses from disk."""
        if not self.jobs_file.exists():
            # Try to reconstruct from cache if no job file exists
            self._reconstruct_jobs_from_cache()
            return

        try:
            data = file_manager.load_json(str(self.jobs_file)) or {}

            for job_id, job_data in data.items():
                if job_data.get("status") in {"completed", "failed", "cancelled"}:
                    self.set_job(
                        job_id,
                        JobStatus(
                            job_id=job_data["job_id"],
                            repo_url=job_data["repo_url"],
                            status=job_data["status"],
                            created_at=self._parse_dt(job_data["created_at"]),
                            started_at=self._parse_dt(job_data["started_at"])
                            if job_data.get("started_at")
                            else None,
                            completed_at=self._parse_dt(job_data["completed_at"])
                            if job_data.get("completed_at")
                            else None,
                            error_message=job_data.get("error_message"),
                            progress=job_data.get("progress", ""),
                            docs_path=job_data.get("docs_path"),
                            main_model=job_data.get("main_model"),
                            commit_id=job_data.get("commit_id"),
                            generation_status=job_data.get("generation_status"),
                            degradation_reasons=list(job_data.get("degradation_reasons") or []),
                            module_summary=job_data.get("module_summary"),
                        ),
                    )
            logger.info(
                "Loaded %d completed jobs from disk",
                len([j for j in self.job_status.values() if j.status == "completed"]),
            )
        except Exception as e:
            logger.error("Error loading job statuses: %s", e)

    def _reconstruct_jobs_from_cache(self):
        """Reconstruct job statuses from cache entries for backward compatibility."""
        try:
            cache_entries = self.cache_manager.cache_index
            reconstructed_count = 0

            for repo_hash, cache_entry in cache_entries.items():
                # Extract repo info to create job_id
                from .github_processor import GitHubRepoProcessor

                try:
                    repo_info = GitHubRepoProcessor.get_repo_info(cache_entry.repo_url)
                    job_id = repo_info["full_name"].replace("/", "--")

                    # Only add if job doesn't already exist
                    if job_id not in self.job_status:
                        self.set_job(
                            job_id,
                            JobStatus(
                                job_id=job_id,
                                repo_url=cache_entry.repo_url,
                                status="completed",
                                created_at=cache_entry.created_at,
                                completed_at=cache_entry.created_at,
                                docs_path=cache_entry.docs_path,
                                progress="Reconstructed from cache",
                            ),
                        )
                        reconstructed_count += 1
                except Exception as e:
                    logger.warning("Failed to reconstruct job for %s: %s", cache_entry.repo_url, e)

            if reconstructed_count > 0:
                logger.info("Reconstructed %d job statuses from cache", reconstructed_count)
                self.save_job_statuses()

        except Exception as e:
            logger.error("Error reconstructing jobs from cache: %s", e)

    def save_job_statuses(self):
        """Save job statuses to disk."""
        try:
            # Ensure cache directory exists
            self.jobs_file.parent.mkdir(parents=True, exist_ok=True)

            with self._job_lock:
                jobs_snapshot = {job_id: copy.copy(job) for job_id, job in self.job_status.items()}

            data = {}
            for job_id, job in jobs_snapshot.items():
                data[job_id] = {
                    "job_id": job.job_id,
                    "repo_url": job.repo_url,
                    "status": job.status,
                    "created_at": job.created_at.isoformat(),
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    "error_message": job.error_message,
                    "progress": job.progress,
                    "docs_path": job.docs_path,
                    "main_model": job.main_model,
                    "commit_id": job.commit_id,
                    "generation_status": job.generation_status,
                    "degradation_reasons": list(job.degradation_reasons),
                    "module_summary": job.module_summary,
                }

            file_manager.save_json(data, str(self.jobs_file))
        except Exception as e:
            logger.error("Error saving job statuses: %s", e)

    def _worker_loop(self):
        """Main worker loop."""
        while self.running:
            try:
                if not self.processing_queue.empty():
                    job_id = self.processing_queue.get(timeout=1)
                    self._process_job(job_id)
                else:
                    time.sleep(1)
            except Exception as e:
                logger.error("Worker error: %s", e)
                time.sleep(1)

    def _load_runtime_config(self, *, repo_path: str, docs_dir: str):
        """Load CodeWikiConfig from self.config_path, raising RuntimeError on failure."""
        if not self.config_path:
            raise RuntimeError("BackgroundWorker requires config_path for generation")
        try:
            return load_config(
                Path(self.config_path),
                repo_path=repo_path,
                overrides=RuntimeOverrides(output_dir=docs_dir),
                context="web",
            )
        except (FileNotFoundError, ValueError) as exc:
            raise RuntimeError(f"Failed to load config '{self.config_path}': {exc}") from exc

    @staticmethod
    def _parse_dt(raw_value: str) -> datetime:
        dt = datetime.fromisoformat(raw_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _process_job(self, job_id: str):
        """Process a single documentation generation job."""
        with self._job_lock:
            if job_id not in self.job_status:
                return
            job = self.job_status[job_id]

        temp_repo_dir: str | None = None
        token = CancellationToken()
        with self._job_lock:
            self._cancel_tokens[job_id] = token

        try:
            with self._job_lock:
                job.status = "processing"
                job.started_at = datetime.now(timezone.utc)
                job.progress = "Starting repository clone..."

            cached_docs = self.cache_manager.get_cached_docs(job.repo_url, job.commit_id)
            if cached_docs and Path(cached_docs).exists():
                with self._job_lock:
                    job.status = "completed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.docs_path = cached_docs
                    job.progress = "Documentation retrieved from cache"
                logger.info("Job %s: using cached documentation", job_id)
                return

            repo_info = GitHubRepoProcessor.get_repo_info(job.repo_url)
            temp_repo_dir = os.path.join(self.temp_dir, job_id)

            with self._job_lock:
                job.progress = f"Cloning repository {repo_info['full_name']}..."

            if not GitHubRepoProcessor.clone_repository(
                repo_info["clone_url"], temp_repo_dir, job.commit_id
            ):
                raise Exception("Failed to clone repository")

            with self._job_lock:
                job.progress = "Analyzing repository structure..."

            docs_dir = os.path.join("output", "docs", f"{job_id}-docs")
            config = self._load_runtime_config(
                repo_path=temp_repo_dir,
                docs_dir=docs_dir,
            )
            with self._job_lock:
                job.main_model = config.main_model
                job.progress = "Generating documentation..."

            doc_generator = DocumentationGenerator(config, job.commit_id, cancel_token=token)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(doc_generator.run())
            finally:
                loop.close()

            with self._job_lock:
                job.generation_status = result.status
                job.degradation_reasons = list(result.warnings)
                job.module_summary = (
                    result.module_summary.to_dict() if result.module_summary else None
                )

            if result.status == "failed":
                raise RuntimeError("; ".join(result.warnings) or "documentation generation failed")
            if result.status == "degraded":
                logger.warning(
                    "Job %s: documentation generated with issues: %s",
                    job_id,
                    "; ".join(result.warnings),
                )

            docs_path = os.path.abspath(config.docs_dir)
            self.cache_manager.add_to_cache(job.repo_url, docs_path, job.commit_id)

            with self._job_lock:
                job.status = "completed"
                job.completed_at = datetime.now(timezone.utc)
                job.docs_path = docs_path
                job.progress = (
                    "Documentation generation completed with issues"
                    if result.status == "degraded"
                    else "Documentation generation completed"
                )

            logger.info("Job %s: documentation generated successfully", job_id)

        except CancellationError as e:
            with self._job_lock:
                job.status = "cancelled"
                job.completed_at = datetime.now(timezone.utc)
                job.error_message = str(e)
                job.progress = "Cancelled by user"
                job.generation_status = "cancelled"
            logger.info("Job %s: cancelled", job_id)
        except Exception as e:
            with self._job_lock:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.error_message = str(e)
                job.progress = f"Failed: {str(e)}"
                job.generation_status = job.generation_status or "failed"
            logger.error("Job %s: failed with error: %s", job_id, e)

        finally:
            with self._job_lock:
                self._cancel_tokens.pop(job_id, None)
            if temp_repo_dir and os.path.exists(temp_repo_dir):
                try:
                    shutil.rmtree(temp_repo_dir)
                    logger.info("Cleaned up temp directory: %s", temp_repo_dir)
                except Exception as e:
                    logger.error("Failed to cleanup temp directory %s: %s", temp_repo_dir, e)
            self.save_job_statuses()
