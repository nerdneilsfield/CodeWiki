"""
Call Graph Analyzer

Central orchestrator for multi-language call graph analysis.
Coordinates language-specific analyzers to build comprehensive call graphs
across different programming languages in a repository.
"""

from typing import Dict, List
import logging
import traceback
from pathlib import Path
from codewiki.src.be.dependency_analyzer.models.core import Node, CallRelationship
from codewiki.src.be.dependency_analyzer.utils.patterns import CODE_EXTENSIONS
from codewiki.src.be.dependency_analyzer.utils.security import safe_open_text

logger = logging.getLogger(__name__)


class CallGraphAnalyzer:
    def __init__(self):
        """Initialize the call graph analyzer."""
        self.functions: Dict[str, Node] = {}
        self.call_relationships: List[CallRelationship] = []
        logger.debug("CallGraphAnalyzer initialized.")

    def analyze_code_files(self, code_files: List[Dict], base_dir: str) -> Dict:
        """
        Complete analysis: Analyze all files to build complete call graph with all nodes.

        This approach:
        1. Analyzes all code files 
        2. Extracts all functions and relationships
        3. Builds complete call graph
        4. Returns all nodes and relationships 
        """
        logger.debug(f"Starting analysis of {len(code_files)} files")

        self.functions = {}
        self.call_relationships = []

        files_analyzed = 0
        for file_info in code_files:
            logger.debug(f"Analyzing: {file_info['path']}")
            self._analyze_code_file(base_dir, file_info)
            files_analyzed += 1
        logger.debug(
            f"Analysis complete: {files_analyzed} files analyzed, {len(self.functions)} functions, {len(self.call_relationships)} relationships"
        )

        logger.debug("Resolving call relationships")
        self._resolve_call_relationships()
        self._pair_header_source_files()
        data_flow_result = self._analyze_data_flow()
        self._deduplicate_relationships()
        viz_data = self._generate_visualization_data()

        return {
            "call_graph": {
                "total_functions": len(self.functions),
                "total_calls": len(self.call_relationships),
                "languages_found": list(set(f.get("language") for f in code_files)),
                "files_analyzed": files_analyzed,
                "analysis_approach": "complete_unlimited",
            },
            "functions": [func.model_dump() for func in self.functions.values()],
            "relationships": [rel.model_dump() for rel in self.call_relationships],
            "visualization": viz_data,
            "data_flow": data_flow_result,
        }

    def extract_code_files(self, file_tree: Dict) -> List[Dict]:
        """
        Extract code files from file tree structure.

        Filters files based on supported extensions and excludes test/config files.

        Args:
            file_tree: Nested dictionary representing file structure

        Returns:
            List of code file information dictionaries
        """
        code_files = []

        def traverse(tree):
            if tree["type"] == "file":
                ext = tree.get("extension", "").lower()
                file_name = tree.get("name", "")
                # Detect by extension, with special-case for CMakeLists.txt and Makefile
                if file_name == "CMakeLists.txt":
                    language = "cmake"
                elif file_name in ("Makefile", "GNUmakefile") or file_name.endswith(".mk") or file_name.endswith(".mak"):
                    language = "makefile"
                elif ext == ".cfg":
                    # .cfg is a common extension — only route as vitis_cfg if content
                    # contains Vitis-specific markers (detected later during analysis)
                    language = "vitis_cfg"
                elif ext in CODE_EXTENSIONS:
                    language = CODE_EXTENSIONS[ext]
                else:
                    language = None
                if language:
                    name = file_name.lower()
                    if not any(skip in name for skip in []):
                        code_files.append(
                            {
                                "path": tree["path"],
                                "name": tree["name"],
                                "extension": ext,
                                "language": language,
                            }
                        )
            elif tree["type"] == "directory" and tree.get("children"):
                for child in tree["children"]:
                    traverse(child)

        traverse(file_tree)
        return code_files

    def _analyze_code_file(self, repo_dir: str, file_info: Dict):
        """
        Analyze a single code file based on its language.

        Routes to appropriate language-specific analyzer.

        Args:
            repo_dir: Repository directory path
            file_info: File information dictionary
        """

        base = Path(repo_dir)
        file_path = base / file_info["path"]

        try:
            content = safe_open_text(base, file_path)
            language = file_info["language"]

            # ── Scripting / dynamic languages ─────────────────────────────
            if language == "python":
                self._analyze_python_file(file_path, content, repo_dir)
            elif language == "javascript":
                self._analyze_javascript_file(file_path, content, repo_dir)
            elif language == "typescript":
                self._analyze_typescript_file(file_path, content, repo_dir)
            elif language == "php":
                self._analyze_php_file(file_path, content, repo_dir)

            # ── JVM / managed languages ────────────────────────────────────
            elif language == "java":
                self._analyze_java_file(file_path, content, repo_dir)
            elif language == "csharp":
                self._analyze_csharp_file(file_path, content, repo_dir)

            # ── Systems languages ──────────────────────────────────────────
            elif language == "c":
                self._analyze_c_file(file_path, content, repo_dir)
            elif language == "cpp":
                self._analyze_cpp_file(file_path, content, repo_dir)
            elif language == "go":
                self._analyze_go_file(file_path, content, repo_dir)
            elif language == "rust":
                self._analyze_rust_file(file_path, content, repo_dir)

            # ── Shell / build / config ─────────────────────────────────────
            elif language == "bash":
                self._analyze_bash_file(file_path, content, repo_dir)
            elif language == "cmake":
                self._analyze_cmake_file(file_path, content, repo_dir)
            elif language == "toml":
                self._analyze_toml_file(file_path, content, repo_dir)
            elif language == "vitis_cfg":
                self._analyze_vitis_cfg_file(file_path, content, repo_dir)
            elif language == "makefile":
                self._analyze_makefile_file(file_path, content, repo_dir)
            elif language == "tcl":
                self._analyze_tcl_file(file_path, content, repo_dir)

        except Exception as e:
            logger.error(f"⚠️ Error analyzing {file_path}: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")

    # ── Scripting / dynamic languages ─────────────────────────────────────

    def _analyze_python_file(self, file_path: str, content: str, base_dir: str):
        """Analyze Python file using the native AST analyzer."""
        from codewiki.src.be.dependency_analyzer.analyzers.python import analyze_python_file
        try:
            functions, relationships = analyze_python_file(file_path, content, repo_path=base_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze Python file {file_path}: {e}", exc_info=True)

    def _analyze_javascript_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze JavaScript file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.javascript import analyze_javascript_file_treesitter
        try:
            functions, relationships = analyze_javascript_file_treesitter(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze JavaScript file {file_path}: {e}", exc_info=True)

    def _analyze_typescript_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze TypeScript file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.typescript import analyze_typescript_file_treesitter
        try:
            functions, relationships = analyze_typescript_file_treesitter(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze TypeScript file {file_path}: {e}", exc_info=True)

    def _analyze_php_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze PHP file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.php import analyze_php_file
        try:
            functions, relationships = analyze_php_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze PHP file {file_path}: {e}", exc_info=True)

    # ── JVM / managed languages ────────────────────────────────────────────

    def _analyze_java_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze Java file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.java import analyze_java_file
        try:
            functions, relationships = analyze_java_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze Java file {file_path}: {e}", exc_info=True)

    def _analyze_csharp_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze C# file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.csharp import analyze_csharp_file
        try:
            functions, relationships = analyze_csharp_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze C# file {file_path}: {e}", exc_info=True)

    # ── Systems languages ──────────────────────────────────────────────────

    def _analyze_c_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze C file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.c import analyze_c_file
        try:
            functions, relationships = analyze_c_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze C file {file_path}: {e}", exc_info=True)

    def _analyze_cpp_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze C++ file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file
        try:
            functions, relationships = analyze_cpp_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze C++ file {file_path}: {e}", exc_info=True)

    def _analyze_go_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze Go file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.go import analyze_go_file
        try:
            functions, relationships = analyze_go_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze Go file {file_path}: {e}", exc_info=True)

    def _analyze_rust_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze Rust file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.rust import analyze_rust_file
        try:
            functions, relationships = analyze_rust_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze Rust file {file_path}: {e}", exc_info=True)

    # ── Shell / build / config ─────────────────────────────────────────────

    def _analyze_bash_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze Bash/Shell file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.bash import analyze_bash_file
        try:
            functions, relationships = analyze_bash_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze Bash file {file_path}: {e}", exc_info=True)

    def _analyze_cmake_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze CMake file using tree-sitter."""
        from codewiki.src.be.dependency_analyzer.analyzers.cmake import analyze_cmake_file
        try:
            functions, relationships = analyze_cmake_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze CMake file {file_path}: {e}", exc_info=True)

    def _analyze_toml_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze TOML file using tree-sitter (extracts top-level tables as structural nodes)."""
        from codewiki.src.be.dependency_analyzer.analyzers.toml import analyze_toml_file
        try:
            functions, relationships = analyze_toml_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze TOML file {file_path}: {e}", exc_info=True)

    def _analyze_tcl_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze Vitis HLS TCL script."""
        from codewiki.src.be.dependency_analyzer.analyzers.tcl import analyze_tcl_file
        try:
            functions, relationships = analyze_tcl_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze TCL file {file_path}: {e}", exc_info=True)

    def _analyze_makefile_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze Makefile using tree-sitter-make."""
        from codewiki.src.be.dependency_analyzer.analyzers.makefile import analyze_makefile_file
        try:
            functions, relationships = analyze_makefile_file(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze Makefile {file_path}: {e}", exc_info=True)

    # Vitis .cfg markers — must be present in file content for it to be treated as Vitis config.
    # These are unique to Xilinx/AMD Vitis and would not appear in generic .cfg files.
    _VITIS_CFG_SECTION_MARKERS = frozenset({"[hls]", "[connectivity]", "[clock]", "[profile]", "[advanced]"})
    _VITIS_CFG_KEY_MARKERS = frozenset({"syn.top=", "syn.file=", "stream_connect=", "nk=", "flow_target=vitis"})

    def _is_vitis_cfg(self, content: str) -> bool:
        """Return True only if the file content looks like a Vitis/HLS .cfg file."""
        lines = [l.strip().lower() for l in content.splitlines()]
        for line in lines:
            if line in self._VITIS_CFG_SECTION_MARKERS:
                return True
            if any(line.startswith(k) for k in self._VITIS_CFG_KEY_MARKERS):
                return True
        return False

    def _analyze_vitis_cfg_file(self, file_path: str, content: str, repo_dir: str):
        """Analyze Vitis .cfg file for HLS top functions, stream connections, memory maps."""
        if not self._is_vitis_cfg(content):
            logger.debug(f"Skipping {file_path}: does not look like a Vitis .cfg file")
            return
        from codewiki.src.be.dependency_analyzer.analyzers.vitis_cfg import analyze_vitis_cfg
        try:
            functions, relationships = analyze_vitis_cfg(file_path, content, repo_path=repo_dir)
            for func in functions:
                self.functions[func.id or f"{file_path}:{func.name}"] = func
            self.call_relationships.extend(relationships)
        except Exception as e:
            logger.error(f"Failed to analyze Vitis cfg {file_path}: {e}", exc_info=True)

    def _analyze_data_flow(self) -> dict:
        """Run cross-file data flow analysis."""
        from codewiki.src.be.dependency_analyzer.analysis.data_flow_analyzer import DataFlowAnalyzer
        analyzer = DataFlowAnalyzer(self.functions, self.call_relationships)
        return analyzer.analyze()

    def _pair_header_source_files(self):
        """Pair header files (.h/.hpp) with implementation files (.cpp/.cc/.c) by basename."""
        from collections import defaultdict
        header_exts = {".h", ".hpp", ".hxx"}
        source_exts = {".c", ".cpp", ".cc", ".cxx", ".c++"}

        # Group nodes by file stem (without extension)
        stem_to_files = defaultdict(lambda: {"headers": [], "sources": []})
        for func_id, func in self.functions.items():
            p = Path(func.file_path)
            stem = p.stem
            if p.suffix in header_exts:
                stem_to_files[stem]["headers"].append(func)
            elif p.suffix in source_exts:
                stem_to_files[stem]["sources"].append(func)

        # Create header_impl relationships for matched pairs
        for stem, files in stem_to_files.items():
            if files["headers"] and files["sources"]:
                header_rep = files["headers"][0]
                source_rep = files["sources"][0]
                self.call_relationships.append(CallRelationship(
                    caller=source_rep.id,
                    callee=header_rep.id,
                    call_line=0,
                    is_resolved=True,
                    relationship_type="header_impl",
                ))

    def _resolve_call_relationships(self):
        """
        Resolve function call relationships across all languages.

        Attempts to match function calls to actual function definitions,
        handling cross-language calls where possible.
        """
        func_lookup = {}
        for func_id, func_info in self.functions.items():
            func_lookup[func_id] = func_id
            func_lookup[func_info.name] = func_id
            if func_info.component_id:
                func_lookup[func_info.component_id] = func_id
                method_name = func_info.component_id.split(".")[-1]
                if method_name not in func_lookup:
                    func_lookup[method_name] = func_id

        resolved_count = 0
        for relationship in self.call_relationships:
            callee_name = relationship.callee

            if callee_name in func_lookup:
                relationship.callee = func_lookup[callee_name]
                relationship.is_resolved = True
                resolved_count += 1
            elif "." in callee_name:
                if callee_name in func_lookup:
                    relationship.callee = func_lookup[callee_name]
                    relationship.is_resolved = True
                    resolved_count += 1
                else:
                    method_name = callee_name.split(".")[-1]
                    if method_name in func_lookup:
                        relationship.callee = func_lookup[method_name]
                        relationship.is_resolved = True
                        resolved_count += 1

    def _deduplicate_relationships(self):
        """
        Deduplicate call relationships based on caller-callee pairs.

        Removes duplicate relationships while preserving the first occurrence.
        This helps eliminate noise from multiple calls to the same function.
        """
        seen = set()
        unique_relationships = []

        for rel in self.call_relationships:
            key = (rel.caller, rel.callee)
            if key not in seen:
                seen.add(key)
                unique_relationships.append(rel)

        self.call_relationships = unique_relationships

    def _generate_visualization_data(self) -> Dict:
        """
        Generate visualization data for graph rendering.

        Creates Cytoscape.js compatible graph data with nodes and edges.

        Returns:
            Dict: Visualization data with cytoscape elements and summary
        """
        cytoscape_elements = []

        for func_id, func_info in self.functions.items():
            node_classes = []
            if func_info.node_type == "method":
                node_classes.append("node-method")
            else:
                node_classes.append("node-function")

            file_ext = Path(func_info.file_path).suffix.lower()
            if file_ext == ".py":
                node_classes.append("lang-python")
            elif file_ext == ".js":
                node_classes.append("lang-javascript")
            elif file_ext == ".ts":
                node_classes.append("lang-typescript")
            elif file_ext in [".c", ".h"]:
                node_classes.append("lang-c")
            elif file_ext in [".cpp", ".cc", ".cxx", ".hpp", ".hxx"]:
                node_classes.append("lang-cpp")
            elif file_ext in [".php", ".phtml", ".inc"]:
                node_classes.append("lang-php")

            cytoscape_elements.append(
                {
                    "data": {
                        "id": func_id,
                        "label": func_info.name,
                        "file": func_info.file_path,
                        "type": func_info.node_type or "function",
                        "language": CODE_EXTENSIONS.get(file_ext, "unknown"),
                    },
                    "classes": " ".join(node_classes),
                }
            )

        resolved_rels = [r for r in self.call_relationships if r.is_resolved]
        for rel in resolved_rels:
            cytoscape_elements.append(
                {
                    "data": {
                        "id": f"{rel.caller}->{rel.callee}",
                        "source": rel.caller,
                        "target": rel.callee,
                        "line": rel.call_line,
                    },
                    "classes": "edge-call",
                }
            )

        summary = {
            "total_nodes": len(self.functions),
            "total_edges": len(resolved_rels),
            "unresolved_calls": len(self.call_relationships) - len(resolved_rels),
        }

        return {
            "cytoscape": {"elements": cytoscape_elements},
            "summary": summary,
        }

    def generate_llm_format(self) -> Dict:
        """Generate clean format optimized for LLM consumption."""
        return {
            "functions": [
                {
                    "name": func.name,
                    "file": Path(func.file_path).name,
                    "purpose": (func.docstring.split("\n")[0] if func.docstring else None),
                    "parameters": func.parameters,
                    "is_recursive": func.name
                    in [
                        rel.callee
                        for rel in self.call_relationships
                        if rel.caller.endswith(func.name)
                    ],
                }
                for func in self.functions.values()
            ],
            "relationships": {
                func.name: {
                    "calls": [
                        rel.callee.split(":")[-1]
                        for rel in self.call_relationships
                        if rel.caller.endswith(func.name) and rel.is_resolved
                    ],
                    "called_by": [
                        rel.caller.split(":")[-1]
                        for rel in self.call_relationships
                        if rel.callee.endswith(func.name) and rel.is_resolved
                    ],
                }
                for func in self.functions.values()
            },
        }

    def _select_most_connected_nodes(self, target_count: int):
        """
        Select the most connected nodes from the call graph.

        Args:
            target_count: The number of nodes to select
        """
        if len(self.functions) <= target_count:
            return

        if not self.call_relationships:
            logger.warning("No call relationships found - keeping all functions by name")
            func_ids = list(self.functions.keys())[:target_count]
            self.functions = {fid: func for fid, func in self.functions.items() if fid in func_ids}
            return

        graph = {}
        for rel in self.call_relationships:
            if rel.caller in self.functions:
                if rel.caller not in graph:
                    graph[rel.caller] = set()
            if rel.callee in self.functions:
                if rel.callee not in graph:
                    graph[rel.callee] = set()

            if rel.caller in graph and rel.callee in graph:
                graph[rel.caller].add(rel.callee)
                graph[rel.callee].add(rel.caller)

        degree_centrality = {}
        for func_id in self.functions.keys():
            degree_centrality[func_id] = len(graph.get(func_id, set()))

        sorted_func_ids = sorted(degree_centrality, key=degree_centrality.get, reverse=True)

        selected_func_ids = sorted_func_ids[:target_count]

        original_func_count = len(self.functions)
        self.functions = {
            fid: func for fid, func in self.functions.items() if fid in selected_func_ids
        }

        original_rel_count = len(self.call_relationships)
        self.call_relationships = [
            rel
            for rel in self.call_relationships
            if rel.caller in selected_func_ids and rel.callee in selected_func_ids
        ]

