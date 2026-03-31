"""Lint report: structured output of all post-processing issues."""
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone


class LintError(Exception):
    """Raised in strict mode when unfixable lint issues remain."""

    def __init__(self, report: "LintReport"):
        self.report = report
        super().__init__(f"Lint failed: {report.summary()}")


@dataclass
class LintReport:
    """Structured report of all lint issues found during post-processing."""

    mermaid_failures: list[dict] = field(default_factory=list)
    # Each: {"file": str, "block_index": int, "error": str, "degraded": bool}

    math_failures: list[dict] = field(default_factory=list)
    # Each: {"file": str, "expression": str, "error": str, "degraded": bool}

    link_issues: list[dict] = field(default_factory=list)
    # Each: {"file": str, "line": int, "target": str, "issue_type": str}

    total_files: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def has_failures(self) -> bool:
        return bool(self.mermaid_failures or self.math_failures or self.link_issues)

    def summary(self) -> str:
        parts = []
        if self.mermaid_failures:
            parts.append(f"{len(self.mermaid_failures)} mermaid")
        if self.math_failures:
            parts.append(f"{len(self.math_failures)} math")
        if self.link_issues:
            parts.append(f"{len(self.link_issues)} link")
        if not parts:
            return "No issues found"
        return f"Issues: {', '.join(parts)} ({self.total_files} files scanned)"

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "total_files": self.total_files,
                "mermaid_failures": self.mermaid_failures,
                "math_failures": self.math_failures,
                "link_issues": self.link_issues,
                "summary": self.summary(),
            },
            indent=2,
            ensure_ascii=False,
        )

    def save(self, docs_dir: str) -> None:
        import os

        path = os.path.join(docs_dir, "_lint_report.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())
