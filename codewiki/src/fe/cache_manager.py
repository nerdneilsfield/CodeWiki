#!/usr/bin/env python3
"""
Cache management for documentation generation results.
"""

import hashlib
import logging
import atexit
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict

from .models import CacheEntry
from .config import WebAppConfig
from codewiki.src.utils import file_manager

logger = logging.getLogger(__name__)


class CacheManager:
    """Manages documentation cache."""

    def __init__(self, cache_dir: str | None = None, cache_expiry_days: int | None = None):
        self.cache_dir = Path(cache_dir or WebAppConfig.CACHE_DIR)
        self.cache_expiry_days = cache_expiry_days or WebAppConfig.CACHE_EXPIRY_DAYS
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_index: Dict[str, CacheEntry] = {}
        self._dirty = False
        self.load_cache_index()
        atexit.register(self.flush)

    @staticmethod
    def _parse_dt(raw_value: str) -> datetime:
        dt = datetime.fromisoformat(raw_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def load_cache_index(self):
        """Load cache index from disk."""
        index_file = self.cache_dir / "cache_index.json"
        if index_file.exists():
            try:
                data = file_manager.load_json(str(index_file)) or {}
                for key, value in data.items():
                    self.cache_index[key] = CacheEntry(
                        repo_url=value["repo_url"],
                        repo_url_hash=value["repo_url_hash"],
                        docs_path=value["docs_path"],
                        created_at=self._parse_dt(value["created_at"]),
                        last_accessed=self._parse_dt(value["last_accessed"]),
                    )
            except Exception as e:
                logger.error("Error loading cache index: %s", e)

    def save_cache_index(self):
        """Save cache index to disk."""
        index_file = self.cache_dir / "cache_index.json"
        try:
            data = {}
            for key, entry in self.cache_index.items():
                data[key] = {
                    "repo_url": entry.repo_url,
                    "repo_url_hash": entry.repo_url_hash,
                    "docs_path": entry.docs_path,
                    "created_at": entry.created_at.isoformat(),
                    "last_accessed": entry.last_accessed.isoformat(),
                }

            file_manager.save_json(data, str(index_file))
        except Exception as e:
            logger.error("Error saving cache index: %s", e)

    def flush(self):
        """Write cache index to disk if dirty."""
        if self._dirty:
            self.save_cache_index()
            self._dirty = False

    def get_repo_hash(self, repo_url: str, commit_id: Optional[str] = None) -> str:
        """Generate hash for repository URL and optional commit ID.

        Including commit_id ensures that cached docs for the same repo at
        different commits are stored and retrieved independently, preventing
        stale docs from being returned when the commit changes.
        """
        key = repo_url if not commit_id else f"{repo_url}@{commit_id}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def get_cached_docs(self, repo_url: str, commit_id: Optional[str] = None) -> Optional[str]:
        """Get cached documentation path if available."""
        repo_hash = self.get_repo_hash(repo_url, commit_id)

        if repo_hash in self.cache_index:
            entry = self.cache_index[repo_hash]

            # Check if cache is still valid
            if datetime.now(timezone.utc) - entry.created_at < timedelta(
                days=self.cache_expiry_days
            ):
                # Update last accessed
                entry.last_accessed = datetime.now(timezone.utc)
                self._dirty = True
                return entry.docs_path
            else:
                # Cache expired, remove it
                self.remove_from_cache(repo_url, commit_id)

        return None

    def add_to_cache(self, repo_url: str, docs_path: str, commit_id: Optional[str] = None):
        """Add documentation to cache."""
        repo_hash = self.get_repo_hash(repo_url, commit_id)
        now = datetime.now(timezone.utc)

        self.cache_index[repo_hash] = CacheEntry(
            repo_url=repo_url,
            repo_url_hash=repo_hash,
            docs_path=docs_path,
            created_at=now,
            last_accessed=now,
        )

        self._dirty = True
        self.flush()

    def remove_from_cache(self, repo_url: str, commit_id: Optional[str] = None):
        """Remove documentation from cache."""
        repo_hash = self.get_repo_hash(repo_url, commit_id)
        if repo_hash in self.cache_index:
            del self.cache_index[repo_hash]
            self._dirty = True
            self.flush()

    def cleanup_expired_cache(self):
        """Remove expired cache entries."""
        expired_entries = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.cache_expiry_days)

        for repo_hash, entry in self.cache_index.items():
            if entry.created_at < cutoff:
                expired_entries.append(repo_hash)

        for repo_hash in expired_entries:
            del self.cache_index[repo_hash]

        if expired_entries:
            self._dirty = True
            self.flush()
