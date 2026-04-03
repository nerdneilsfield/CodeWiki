#!/usr/bin/env python3
"""
FastAPI route handlers for the CodeWiki web application.
"""

import logging
import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import asdict

from fastapi import Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .models import JobStatus, JobStatusResponse
from .github_processor import GitHubRepoProcessor
from .background_worker import BackgroundWorker
from .cache_manager import CacheManager
from .templates import WEB_INTERFACE_TEMPLATE
from .template_utils import render_template
from .config import WebAppConfig
from codewiki.src.utils import file_manager, module_doc_filename, find_module_doc

logger = logging.getLogger(__name__)

_COMMIT_RE = re.compile(r"^[a-fA-F0-9]{4,40}$")


class WebRoutes:
    """Handles all web routes for the application."""

    def __init__(self, background_worker: BackgroundWorker, cache_manager: CacheManager):
        self.background_worker = background_worker
        self.cache_manager = cache_manager

    def _snapshot_jobs(self) -> dict[str, JobStatus]:
        """Read job statuses via the worker snapshot API when available."""
        if hasattr(self.background_worker, "snapshot_jobs"):
            return self.background_worker.snapshot_jobs()

        job_status = getattr(self.background_worker, "job_status", {})
        return dict(job_status)

    def _snapshot_job(self, job_id: str):
        """Read a single job via the worker snapshot API when available."""
        if hasattr(self.background_worker, "snapshot_job"):
            return self.background_worker.snapshot_job(job_id)
        if hasattr(self.background_worker, "get_job_status"):
            return self.background_worker.get_job_status(job_id)

        job_status = getattr(self.background_worker, "job_status", {})
        return job_status.get(job_id)

    def _set_job(self, job_id: str, job: JobStatus) -> None:
        """Write job state through the worker API when available."""
        if hasattr(self.background_worker, "set_job"):
            self.background_worker.set_job(job_id, job)
            return

        job_status = getattr(self.background_worker, "job_status", None)
        if isinstance(job_status, dict):
            job_status[job_id] = job

    async def index_get(self, request: Request) -> HTMLResponse:
        """Main page with form for submitting GitHub repositories."""
        # Clean up old jobs before displaying
        # self.cleanup_old_jobs()

        # Get recent jobs (last 10)
        all_jobs = self._snapshot_jobs()
        recent_jobs = sorted(all_jobs.values(), key=lambda x: x.created_at, reverse=True)[:100]

        context = {
            "message": None,
            "message_type": None,
            "repo_url": "",
            "commit_id": "",
            "recent_jobs": recent_jobs,
        }

        return HTMLResponse(content=render_template(WEB_INTERFACE_TEMPLATE, context))

    async def index_post(
        self, request: Request, repo_url: str = Form(...), commit_id: str = Form("")
    ) -> HTMLResponse:
        """Handle repository submission."""
        # Clean up old jobs before processing
        self.cleanup_old_jobs()

        message = None
        message_type = None

        repo_url = repo_url.strip()
        commit_id = commit_id.strip().lower() if commit_id else ""

        if not repo_url:
            message = "Please enter a GitHub repository URL"
            message_type = "error"
        elif not GitHubRepoProcessor.is_valid_github_url(repo_url):
            message = "Please enter a valid GitHub repository URL"
            message_type = "error"
        elif commit_id and not _COMMIT_RE.match(commit_id):
            raise HTTPException(
                status_code=400,
                detail="Invalid commit ID format (expected 4-40 hex characters)",
            )
        else:
            # Normalize the repo URL for comparison
            normalized_repo_url = self._normalize_github_url(repo_url)

            # Get repo info for job ID generation
            repo_info = GitHubRepoProcessor.get_repo_info(normalized_repo_url)
            job_id = self._repo_full_name_to_job_id(repo_info["full_name"])

            # Check if already in queue, processing, or recently failed
            existing_job = self._snapshot_job(job_id)
            recent_cutoff = datetime.now(timezone.utc) - timedelta(
                minutes=WebAppConfig.RETRY_COOLDOWN_MINUTES
            )

            if existing_job:
                if existing_job.status in ["queued", "processing"]:
                    pass  # Will handle below
                elif existing_job.status == "failed" and existing_job.created_at > recent_cutoff:
                    pass  # Will handle below
                else:
                    existing_job = None  # Job is old or completed, can reuse

            if existing_job:
                if existing_job.status in ["queued", "processing"]:
                    message = (
                        f"Repository is already being processed (Job ID: {existing_job.job_id})"
                    )
                else:
                    message = f"Repository recently failed processing. Please wait a few minutes before retrying (Job ID: {existing_job.job_id})"
                message_type = "error"
            else:
                # Check cache
                cached_docs = self.cache_manager.get_cached_docs(
                    normalized_repo_url, commit_id or None
                )
                if cached_docs and Path(cached_docs).exists():
                    message = "Documentation found in cache! Redirecting to view..."
                    message_type = "success"
                    # Create a dummy completed job for display
                    job = JobStatus(
                        job_id=job_id,
                        repo_url=normalized_repo_url,  # Use normalized URL
                        status="completed",
                        created_at=datetime.now(timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                        docs_path=cached_docs,
                        progress="Retrieved from cache",
                        commit_id=commit_id if commit_id else None,
                    )
                    self._set_job(job_id, job)
                    self.background_worker.save_job_statuses()
                else:
                    # Add to queue
                    try:
                        job = JobStatus(
                            job_id=job_id,
                            repo_url=normalized_repo_url,  # Use normalized URL
                            status="queued",
                            created_at=datetime.now(timezone.utc),
                            progress="Waiting in queue...",
                            commit_id=commit_id if commit_id else None,
                        )

                        if self.background_worker.add_job(job_id, job):
                            message = f"Repository added to processing queue! Job ID: {job_id}"
                            message_type = "success"
                            repo_url = ""  # Clear form
                        else:
                            message = "Server is at capacity, please try again later"
                            message_type = "error"

                    except Exception as e:
                        logger.error("Failed to add repository to queue: %s", e, exc_info=True)
                        message = "Internal server error"
                        message_type = "error"

        # Get recent jobs (last 10)
        all_jobs = self._snapshot_jobs()
        recent_jobs = sorted(all_jobs.values(), key=lambda x: x.created_at, reverse=True)

        context = {
            "message": message,
            "message_type": message_type,
            "repo_url": repo_url or "",
            "commit_id": commit_id or "",
            "recent_jobs": recent_jobs,
        }

        return HTMLResponse(content=render_template(WEB_INTERFACE_TEMPLATE, context))

    async def get_job_status(self, job_id: str) -> JobStatusResponse:
        """API endpoint to get job status."""
        job = self._snapshot_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        return JobStatusResponse(**asdict(job))

    async def cancel_job(self, job_id: str) -> JSONResponse:
        """Request cancellation for an active background job."""
        if self.background_worker.cancel_job(job_id):
            return JSONResponse({"status": "cancelling"})
        raise HTTPException(status_code=404, detail="Job not found or not running")

    async def view_docs(self, job_id: str) -> RedirectResponse:
        """View generated documentation."""
        job = self._snapshot_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status != "completed" or not job.docs_path:
            raise HTTPException(status_code=404, detail="Documentation not available")

        docs_path = Path(job.docs_path)
        if not docs_path.exists():
            raise HTTPException(status_code=404, detail="Documentation files not found")

        # Redirect to the documentation viewer
        return RedirectResponse(url=f"/static-docs/{job_id}/", status_code=status.HTTP_302_FOUND)

    async def serve_generated_docs(
        self, job_id: str, filename: str = "overview.md"
    ) -> HTMLResponse:
        """Serve generated documentation files."""
        job = self._snapshot_job(job_id)
        docs_path = None
        repo_url = None

        if job:
            # Job status exists - use it
            if job.status != "completed" or not job.docs_path:
                raise HTTPException(status_code=404, detail="Documentation not available")
            docs_path = Path(job.docs_path)
            repo_url = job.repo_url
        else:
            # No job status - try to find documentation in cache by job_id
            # Convert job_id back to repo full name and construct potential paths
            repo_full_name = self._job_id_to_repo_full_name(job_id)
            potential_repo_url = f"https://github.com/{repo_full_name}"

            # Check if documentation exists in cache (no commit info available here)
            cached_docs = self.cache_manager.get_cached_docs(potential_repo_url, None)
            if cached_docs and Path(cached_docs).exists():
                docs_path = Path(cached_docs)
                repo_url = potential_repo_url

                # Recreate job status for consistency
                job = JobStatus(
                    job_id=job_id,
                    repo_url=potential_repo_url,
                    status="completed",
                    created_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                    docs_path=cached_docs,
                    progress="Loaded from cache",
                    commit_id=None,  # No commit info available from cache
                )
                self._set_job(job_id, job)
                self.background_worker.save_job_statuses()
            else:
                raise HTTPException(status_code=404, detail="Documentation not found")

        if not docs_path or not docs_path.exists():
            raise HTTPException(status_code=404, detail="Documentation files not found")

        try:
            html = await asyncio.to_thread(
                self._render_generated_docs_sync,
                docs_path,
                repo_url,
                job_id,
                filename,
            )
            return HTMLResponse(content=html)
        except HTTPException:
            raise

        except Exception as e:
            logger.error("Error reading %s: %s", filename, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    def _normalize_github_url(self, url: str) -> str:
        """Normalize GitHub URL for consistent comparison."""
        try:
            # Get repo info to standardize the URL format
            repo_info = GitHubRepoProcessor.get_repo_info(url)
            return f"https://github.com/{repo_info['full_name']}"
        except Exception:
            # Fallback to basic normalization
            return url.rstrip("/").lower()

    def _repo_full_name_to_job_id(self, full_name: str) -> str:
        """Convert repo full name to URL-safe job ID."""
        return full_name.replace("/", "--")

    def _job_id_to_repo_full_name(self, job_id: str) -> str:
        """Convert job ID back to repo full name."""
        return job_id.replace("--", "/")

    def _attach_doc_filenames(self, tree, docs_dir, path=None):
        if not tree:
            return
        base = path or []
        for name, info in tree.items():
            module_path = base + [name]
            doc_filename = info.get("_doc_filename")
            if doc_filename:
                found_path = Path(docs_dir) / doc_filename
                info["doc_filename"] = doc_filename
                info["doc_exists"] = found_path.exists()
            else:
                found = find_module_doc(str(docs_dir), module_path)
                if found:
                    info["doc_filename"] = os.path.basename(found)
                    info["doc_exists"] = True
                else:
                    info["doc_filename"] = module_doc_filename(module_path)
                    info["doc_exists"] = False
            children = info.get("children")
            if isinstance(children, dict) and children:
                self._attach_doc_filenames(children, docs_dir, module_path)

    def _render_generated_docs_sync(
        self,
        docs_path: Path,
        repo_url: str,
        job_id: str,
        filename: str,
    ) -> str:
        module_tree = None
        module_tree_file = docs_path / "module_tree.json"
        if module_tree_file.exists():
            try:
                module_tree = file_manager.load_json(str(module_tree_file))
                self._attach_doc_filenames(module_tree, docs_path)
            except Exception:
                pass

        metadata = None
        metadata_file = docs_path / "metadata.json"
        if metadata_file.exists():
            try:
                metadata = file_manager.load_json(str(metadata_file))
            except Exception:
                pass

        file_path = (docs_path / filename).resolve()
        if not file_path.is_relative_to(docs_path.resolve()):
            raise HTTPException(status_code=403, detail="Access denied")
        if not file_path.exists():
            stem = filename.rsplit(".", 1)[0] if "." in filename else filename
            found = None
            tree_file = docs_path / "module_tree.json"
            if tree_file.exists():
                try:
                    tree = file_manager.load_json(str(tree_file)) or {}
                except Exception:
                    tree = None
                if tree:
                    found = self._search_tree_for_stem(tree, docs_path, stem)
            if not found:
                found = find_module_doc(str(docs_path), stem.split("-"))
            if found:
                found_path = Path(found).resolve()
                if not found_path.is_relative_to(docs_path.resolve()):
                    raise HTTPException(status_code=403, detail="Access denied")
                file_path = found_path
            else:
                raise HTTPException(status_code=404, detail=f"File {filename} not found")

        from .visualise_docs import markdown_to_html, get_file_title
        from .templates import DOCS_VIEW_TEMPLATE

        content = file_manager.load_text(str(file_path))
        html_content = markdown_to_html(content, base_url=f"/static-docs/{job_id}/")
        title = get_file_title(file_path)
        context = {
            "repo_name": repo_url.split("/")[-1],
            "title": title,
            "content": html_content,
            "navigation": module_tree,
            "current_page": filename,
            "job_id": job_id,
            "metadata": metadata,
        }
        return render_template(DOCS_VIEW_TEMPLATE, context)

    def _search_tree_for_stem(self, nodes, docs_path: Path, stem: str) -> str | None:
        for _key, _info in nodes.items():
            doc_filename = _info.get("doc_filename") or _info.get("_doc_filename")
            if doc_filename and Path(doc_filename).stem == stem:
                return str((docs_path / doc_filename).resolve())
            children = _info.get("children") or {}
            if children:
                result = self._search_tree_for_stem(children, docs_path, stem)
                if result:
                    return result
        return None

    def cleanup_old_jobs(self):
        """Clean up old job status entries."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=WebAppConfig.JOB_CLEANUP_HOURS)
        all_jobs = self._snapshot_jobs()
        expired_jobs = [
            job_id
            for job_id, job in all_jobs.items()
            if job.created_at < cutoff and job.status in ["completed", "failed"]
        ]

        for job_id in expired_jobs:
            self.background_worker.delete_job(job_id)
