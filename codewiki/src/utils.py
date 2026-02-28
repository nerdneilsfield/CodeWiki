import os
import json
import tempfile
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
    """Build a stable markdown filename for a module path."""
    parts = [p for p in module_path if p]
    if not parts:
        return "overview.md"
    safe_parts = [
        p.strip().replace(" ", "_").replace("/", "_")
        for p in parts
    ]
    return f"{'-'.join(safe_parts)}.md"
