"""
Cross-file data flow analyzer.

Analyzes parameter-level data flow across function calls and tracks
ownership/lifetime patterns (malloc/free, new/delete, smart pointers).
"""

import re
import logging
from typing import Dict, List, Any
from codewiki.src.be.dependency_analyzer.models.core import (
    Node,
    CallRelationship,
    DataFlowEdge,
)

logger = logging.getLogger(__name__)

# Allocation/deallocation function pairs
_ALLOC_FUNCTIONS = {"malloc", "calloc", "realloc", "strdup", "new"}
_DEALLOC_FUNCTIONS = {"free", "delete"}
_OWNERSHIP_TRANSFER = {"std::move"}
_SMART_PTRS = {"unique_ptr", "shared_ptr", "weak_ptr"}


class DataFlowAnalyzer:
    def __init__(self, functions: Dict[str, Node], relationships: List[CallRelationship]):
        self.functions = functions
        self.relationships = relationships

    def analyze(self) -> Dict[str, Any]:
        flow_edges = self._build_flow_edges()
        ownership_patterns = self._detect_ownership_patterns()

        return {
            "flow_edges": flow_edges,
            "ownership_patterns": ownership_patterns,
        }

    def _build_flow_edges(self) -> List[Dict]:
        """Build parameter-level data flow edges from call relationships."""
        edges = []
        for rel in self.relationships:
            if rel.relationship_type not in ("call", "calls", None):
                continue
            callee_func = self.functions.get(rel.callee)
            if not callee_func or not callee_func.parameters:
                continue

            for param in callee_func.parameters:
                param_name = param if isinstance(param, str) else param
                edge = DataFlowEdge(
                    param_name=param_name,
                    direction="in",
                )
                edges.append(
                    {
                        "caller": rel.caller,
                        "callee": rel.callee,
                        "line": rel.call_line,
                        "edge": edge.model_dump(),
                    }
                )
        return edges

    def _detect_ownership_patterns(self) -> List[Dict]:
        """Detect allocation/deallocation and ownership patterns in source code."""
        patterns = []
        alloc_re = re.compile(r"\b(malloc|calloc|realloc|new)\b")
        dealloc_re = re.compile(r"\b(free|delete)\b")
        smart_re = re.compile(r"\b(unique_ptr|shared_ptr|make_unique|make_shared)\b")
        move_re = re.compile(r"\bstd::move\b")

        for func_id, func in self.functions.items():
            if not func.source_code:
                continue
            src = func.source_code

            has_alloc = bool(alloc_re.search(src))
            has_dealloc = bool(dealloc_re.search(src))
            has_smart = bool(smart_re.search(src))
            has_move = bool(move_re.search(src))

            if has_alloc or has_dealloc or has_smart or has_move:
                pattern = {
                    "function": func_id,
                    "file": func.relative_path,
                    "allocates": has_alloc,
                    "deallocates": has_dealloc,
                    "uses_smart_ptr": has_smart,
                    "uses_move": has_move,
                }
                if has_alloc and not has_dealloc and not has_smart:
                    pattern["warning"] = "allocates without deallocation in scope"
                patterns.append(pattern)

        return patterns
