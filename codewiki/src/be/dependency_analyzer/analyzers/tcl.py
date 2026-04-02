"""
Vitis HLS TCL script analyzer.

Parses Vitis/Xilinx HLS TCL scripts to extract:
- Top function (set_top)
- Source files (add_files, excluding -tb testbench files)
- Project name (open_project)
- Synthesis and export operations (csynth_design, export_design)

Uses tree-sitter-language-pack's TCL grammar.
Command AST: command → simple_word(name) + word_list(args...)
"""

import logging
from typing import List, Tuple
from pathlib import Path
import os

from tree_sitter_language_pack import get_language, get_parser
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship

logger = logging.getLogger(__name__)


class VitisHLSTclAnalyzer:
    def __init__(self, file_path: str, content: str, repo_path: str = None):
        self.file_path = str(file_path)
        self.content = content
        self.repo_path = repo_path or ""
        self.nodes: List[Node] = []
        self.call_relationships: List[CallRelationship] = []
        self._analyze()

    def _get_relative_path(self) -> str:
        if self.repo_path:
            try:
                return os.path.relpath(self.file_path, self.repo_path)
            except ValueError:
                return self.file_path
        return self.file_path

    def _get_module_path(self) -> str:
        rel = self._get_relative_path()
        if rel.endswith(".tcl"):
            rel = rel[:-4]
        return rel.replace("/", ".").replace("\\", ".")

    def _get_component_id(self, name: str) -> str:
        module = self._get_module_path()
        return f"{module}.{name}" if module else name

    def _node_text(self, node) -> str:
        return node.text.decode("utf8").strip()

    def _analyze(self):
        lang = get_language("tcl")
        parser = get_parser("tcl")
        tree = parser.parse(bytes(self.content, "utf8"))
        lines = self.content.splitlines()

        top_func_id = None  # set after set_top is found

        for cmd_node in tree.root_node.children:
            if cmd_node.type != "command":
                continue

            # First child is the command name (simple_word)
            name_node = next((c for c in cmd_node.children if c.type == "simple_word"), None)
            if not name_node:
                continue
            cmd_name = self._node_text(name_node)

            # Remaining children form the argument list
            arg_nodes = [c for c in cmd_node.children if c.type == "word_list"]
            # word_list may contain multiple tokens separated by whitespace
            raw_args = " ".join(self._node_text(n) for n in arg_nodes)
            args = raw_args.split()

            start_line = cmd_node.start_point[0] + 1

            if cmd_name == "open_project" and args:
                project_name = args[-1]  # skip flags like -reset
                comp_id = self._get_component_id(project_name)
                self.nodes.append(
                    Node(
                        id=comp_id,
                        name=project_name,
                        component_type="hls_project",
                        file_path=self.file_path,
                        relative_path=self._get_relative_path(),
                        source_code="\n".join(
                            lines[cmd_node.start_point[0] : cmd_node.end_point[0] + 1]
                        ),
                        start_line=start_line,
                        end_line=cmd_node.end_point[0] + 1,
                        node_type="hls_project",
                        display_name=f"HLS project: {project_name}",
                        component_id=comp_id,
                    )
                )

            elif cmd_name == "set_top" and args:
                func_name = args[0]
                comp_id = self._get_component_id(func_name)
                self.nodes.append(
                    Node(
                        id=comp_id,
                        name=func_name,
                        component_type="hls_top",
                        file_path=self.file_path,
                        relative_path=self._get_relative_path(),
                        source_code="\n".join(
                            lines[cmd_node.start_point[0] : cmd_node.end_point[0] + 1]
                        ),
                        start_line=start_line,
                        end_line=cmd_node.end_point[0] + 1,
                        node_type="hls_top",
                        display_name=f"HLS top: {func_name}",
                        component_id=comp_id,
                        is_hls_kernel=True,
                    )
                )
                top_func_id = comp_id

            elif cmd_name == "add_files" and args:
                # Skip testbench files (add_files -tb ...)
                if "-tb" in args:
                    continue
                # Skip pure flags (no actual file)
                file_args = [a for a in args if not a.startswith("-")]
                # Skip -cflags value (next arg after -cflags flag)
                cleaned = []
                skip_next = False
                for a in args:
                    if skip_next:
                        skip_next = False
                        continue
                    if a in ("-cflags", "-csimflags", "-I"):
                        skip_next = True
                        continue
                    if not a.startswith("-"):
                        cleaned.append(a)
                for src_file in cleaned:
                    caller = top_func_id or self._get_component_id("__top__")
                    self.call_relationships.append(
                        CallRelationship(
                            caller=caller,
                            callee=src_file.lstrip("./"),
                            call_line=start_line,
                            is_resolved=False,
                            relationship_type="hls_source",
                        )
                    )

            elif cmd_name == "csynth_design":
                caller = top_func_id or self._get_component_id("__top__")
                self.call_relationships.append(
                    CallRelationship(
                        caller=caller,
                        callee="csynth_design",
                        call_line=start_line,
                        is_resolved=False,
                        relationship_type="hls_synth",
                    )
                )

            elif cmd_name == "export_design":
                # extract -format value and -output value
                fmt = None
                out = None
                for i, a in enumerate(args):
                    if a == "-format" and i + 1 < len(args):
                        fmt = args[i + 1]
                    elif a == "-output" and i + 1 < len(args):
                        out = args[i + 1]
                caller = top_func_id or self._get_component_id("__top__")
                self.call_relationships.append(
                    CallRelationship(
                        caller=caller,
                        callee=out or f"export.{fmt or 'unknown'}",
                        call_line=start_line,
                        is_resolved=False,
                        relationship_type="hls_export",
                    )
                )


def analyze_tcl_file(
    file_path: str,
    content: str,
    repo_path: str = None,
) -> Tuple[List[Node], List[CallRelationship]]:
    analyzer = VitisHLSTclAnalyzer(file_path, content, repo_path)
    return analyzer.nodes, analyzer.call_relationships
