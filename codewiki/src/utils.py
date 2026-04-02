import os
import json
import tempfile
import re
import hashlib
from typing import Any, Optional, Dict, List


# ------------------------------------------------------------
# ---------------------- File Manager ---------------------
# ------------------------------------------------------------

class FileManager:
    """Handles file I/O operations."""
    
    @staticmethod
    def ensure_directory(path: str) -> None:
        """Create directory if it doesn't exist."""
        os.makedirs(path, exist_ok=True)
    
    @staticmethod
    def save_json(data: Any, filepath: str) -> None:
        """Save data as JSON to file."""
        parent_dir = os.path.dirname(os.path.abspath(filepath)) or "."
        os.makedirs(parent_dir, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=parent_dir)
        try:
            with os.fdopen(tmp_fd, 'w') as f:
                json.dump(data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
    
    @staticmethod
    def load_json(filepath: str) -> Optional[Dict[str, Any]]:
        """Load JSON from file, return None if file doesn't exist."""
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    
    @staticmethod
    def save_text(content: str, filepath: str) -> None:
        """Save text content to file."""
        with open(filepath, 'w') as f:
            f.write(content)
    
    @staticmethod
    def load_text(filepath: str) -> str:
        """Load text content from file."""
        with open(filepath, 'r') as f:
            return f.read()

file_manager = FileManager()


def module_doc_filename(module_path: List[str]) -> str:
    """Build a stable markdown filename for a module path.

    Hyphens inside part names are normalised to underscores so that ``-``
    only appears as the separator between parts.  This makes filenames
    unambiguous and stable across LLM clustering runs that may use hyphens
    or spaces interchangeably.
    """
    def _normalize_part(part: str) -> str:
        value = part.strip().lower()
        value = value.replace("&", " and ")
        value = value.replace("/", "_").replace("-", "_")
        value = re.sub(r"[^\w\s]", " ", value)
        value = re.sub(r"\s+", "_", value)
        value = re.sub(r"_+", "_", value)
        return value.strip("_")

    parts = [p for p in module_path if p]
    if not parts:
        return "overview.md"
    safe_parts = [normalized for p in parts if (normalized := _normalize_part(p))]
    if not safe_parts:
        return "overview.md"
    return f"{'-'.join(safe_parts)}.md"


def _normalize_for_match(filename: str) -> str:
    """Normalise a filename for fuzzy comparison.

    Treats ``-``, ``_``, and `` `` as equivalent, collapses runs of
    underscores, and lower-cases the result.
    """
    name = filename.lower().replace("&", " and ")
    name = re.sub(r"[^\w.\s-]", "_", name)
    name = name.replace("-", "_").replace(" ", "_")
    name = re.sub(r"_+", "_", name)
    return name


def content_hash(path: str) -> str:
    """Return a stable hash for file contents, or empty string if missing."""
    try:
        with open(path, "rb") as f:
            digest = hashlib.md5()
            while chunk := f.read(8192):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def doc_id_for_path(tree: Dict[str, Any], module_path: List[str]) -> str:
    """Derive a stable doc/task id from a tree node when possible."""
    if not module_path:
        return "overview:root"
    try:
        node: Dict[str, Any] = tree
        for idx, part in enumerate(module_path):
            if idx == 0:
                node = node[part]
            else:
                node = node["children"][part]
        module_id = node.get("module_id", "")
        if module_id:
            return f"module:{module_id}"
        return f"module:{node.get('_doc_filename', module_doc_filename(module_path)).removesuffix('.md')}"
    except (KeyError, TypeError):
        return f"module:{module_doc_filename(module_path).removesuffix('.md')}"


def find_module_doc(working_dir: str, module_path: List[str]) -> Optional[str]:
    """Find the ``.md`` file for *module_path*, tolerating name differences.

    Returns the absolute path if found, or ``None``.

    Matching strategy (first hit wins):
    1. **Canonical** — exact filename from current ``module_doc_filename``.
    2. **Fuzzy full-path** — ``-``/``_``/`` `` treated as equivalent.
    3. **Fuzzy suffix** — only the last path part is matched as a filename
       suffix, catching files whose parent path changed between runs.
    """
    canonical = module_doc_filename(module_path)
    canonical_path = os.path.join(working_dir, canonical)
    if os.path.exists(canonical_path):
        return canonical_path

    target_full = _normalize_for_match(canonical)

    # The leaf name for suffix matching (e.g. "session_runtime.md")
    leaf = module_path[-1] if module_path else ""
    target_suffix = _normalize_for_match(
        module_doc_filename([leaf]) if leaf else ""
    )

    suffix_candidate: Optional[str] = None
    try:
        for fname in os.listdir(working_dir):
            if not fname.endswith(".md"):
                continue
            normed = _normalize_for_match(fname)
            # Strategy 2: full-path match
            if normed == target_full:
                return os.path.join(working_dir, fname)
            # Strategy 3: suffix match (weaker — remember but don't return yet)
            if target_suffix and suffix_candidate is None:
                # Check if fname ends with -<leaf>.md or _<leaf>.md
                if normed.endswith(target_suffix) or normed == target_suffix:
                    suffix_candidate = os.path.join(working_dir, fname)
    except OSError:
        pass

    return suffix_candidate
