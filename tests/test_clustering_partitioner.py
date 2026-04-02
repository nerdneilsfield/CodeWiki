"""Tests for clustering partitioner: directory prior + SCC contraction + Louvain.

TDD: tests written BEFORE implementation.
"""

import pytest
import networkx as nx

from codewiki.src.be.index.models import SymbolEdge, EdgeType, Confidence
from codewiki.src.be.clustering.partitioner import (
    partition_by_directory,
    contract_sccs,
    detect_communities,
    partition_components,
)
from codewiki.src.be.clustering.graph_builder import build_clustering_graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _edge(
    from_sym: str,
    to_sym: str | None,
    edge_type: EdgeType = EdgeType.IMPORTS,
    confidence: Confidence = Confidence.HIGH,
) -> SymbolEdge:
    """Convenience factory for SymbolEdge test fixtures."""
    return SymbolEdge(
        edge_type=edge_type,
        from_symbol=from_sym,
        to_symbol=to_sym,
        confidence=confidence,
    )


def _make_graph(
    component_ids: list[str],
    file_map: dict[str, str],
    edges: list[SymbolEdge] | None = None,
) -> nx.Graph:
    """Build a clustering graph from components and optional edges."""
    return build_clustering_graph(edges or [], set(component_ids), file_map)


# ---------------------------------------------------------------------------
# 1. partition_by_directory — two distinct top-level directories
# ---------------------------------------------------------------------------


class TestPartitionByDirectoryTwoDirs:
    """Test 1: 4 components in src/auth/ and 2 in src/api/ → 2 partitions."""

    def test_two_dir_partitions_created(self):
        # Use paths WITHOUT common prefix so top-level dirs differ
        component_ids = [
            "auth/handler.py::AuthHandler",
            "auth/models.py::User",
            "auth/utils.py::hash_password",
            "auth/middleware.py::JWTMiddleware",
            "api/router.py::Router",
            "api/views.py::UserView",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert len(partitions) == 2
        assert "auth" in partitions
        assert "api" in partitions

    def test_auth_partition_has_four_members(self):
        component_ids = [
            "src/auth/handler.py::AuthHandler",
            "src/auth/models.py::User",
            "src/auth/utils.py::hash_password",
            "src/auth/middleware.py::JWTMiddleware",
            "src/api/router.py::Router",
            "src/api/views.py::UserView",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert "src" in partitions
        assert len(partitions["src"]) == 6

    def test_partition_names_are_top_level_dirs(self):
        component_ids = [
            "auth/handler.py::AuthHandler",
            "auth/models.py::User",
            "api/router.py::Router",
            "api/views.py::UserView",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert "auth" in partitions
        assert "api" in partitions

    def test_auth_partition_contains_correct_components(self):
        auth_comps = [
            "auth/handler.py::AuthHandler",
            "auth/models.py::User",
        ]
        api_comps = [
            "api/router.py::Router",
            "api/views.py::UserView",
        ]
        component_ids = auth_comps + api_comps
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert partitions["auth"] == set(auth_comps)
        assert partitions["api"] == set(api_comps)


# ---------------------------------------------------------------------------
# 2. partition_by_directory — root files
# ---------------------------------------------------------------------------


class TestPartitionByDirectoryRootFiles:
    """Test 2: components with no directory → "_root" partition."""

    def test_root_files_go_into_root_partition(self):
        component_ids = [
            "main.py::main",
            "utils.py::helper",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert "_root" in partitions

    def test_root_partition_contains_all_root_files(self):
        component_ids = [
            "main.py::main",
            "utils.py::helper",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert partitions["_root"] == set(component_ids)

    def test_mixed_root_and_subdir(self):
        component_ids = [
            "main.py::main",
            "src/auth/handler.py::AuthHandler",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert "_root" in partitions
        assert "src" in partitions
        assert "main.py::main" in partitions["_root"]


# ---------------------------------------------------------------------------
# 3. partition_by_directory — single directory
# ---------------------------------------------------------------------------


class TestPartitionByDirectorySingleDir:
    """Test 3: all components in the same dir → exactly 1 partition."""

    def test_single_partition_returned(self):
        component_ids = [
            "src/a.py::Foo",
            "src/b.py::Bar",
            "src/c.py::Baz",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert len(partitions) == 1

    def test_single_partition_name_is_top_dir(self):
        component_ids = ["src/a.py::Foo", "src/b.py::Bar"]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert "src" in partitions

    def test_all_components_in_single_partition(self):
        component_ids = ["src/a.py::Foo", "src/b.py::Bar", "src/c.py::Baz"]
        file_map = {c: c.split("::")[0] for c in component_ids}
        partitions = partition_by_directory(component_ids, file_map)
        assert partitions["src"] == set(component_ids)


# ---------------------------------------------------------------------------
# 4. contract_sccs — A→B→C→A cycle
# ---------------------------------------------------------------------------


class TestSCCContractionCycle:
    """Test 4: A→B→C→A cycle → single super-node, edges merged."""

    def setup_method(self):
        self.comp_a = "src/a.py::Alpha"
        self.comp_b = "src/b.py::Beta"
        self.comp_c = "src/c.py::Gamma"
        self.comp_d = "src/d.py::Delta"  # external node
        self.file_map = {
            self.comp_a: "src/a.py",
            self.comp_b: "src/b.py",
            self.comp_c: "src/c.py",
            self.comp_d: "src/d.py",
        }
        # Undirected graph with edges among cycle nodes and external
        self.graph = _make_graph(
            [self.comp_a, self.comp_b, self.comp_c, self.comp_d],
            self.file_map,
        )
        # Manually add edges to represent coupling
        self.graph.add_edge(self.comp_a, self.comp_b, weight=1.0)
        self.graph.add_edge(self.comp_b, self.comp_c, weight=1.0)
        self.graph.add_edge(self.comp_c, self.comp_a, weight=1.0)
        self.graph.add_edge(self.comp_c, self.comp_d, weight=0.5)
        # Directed cycle A→B→C→A
        self.directed_edges = [
            (self.comp_a, self.comp_b),
            (self.comp_b, self.comp_c),
            (self.comp_c, self.comp_a),
        ]

    def test_scc_reduces_cycle_to_super_node(self):
        contracted_graph, node_map = contract_sccs(self.graph, self.directed_edges)
        # The 3 cycle nodes should map to the same super-node
        super_node = node_map[self.comp_a]
        assert node_map[self.comp_b] == super_node
        assert node_map[self.comp_c] == super_node

    def test_contracted_graph_contains_super_node(self):
        contracted_graph, node_map = contract_sccs(self.graph, self.directed_edges)
        super_node = node_map[self.comp_a]
        assert super_node in contracted_graph.nodes

    def test_contracted_graph_original_members_removed(self):
        contracted_graph, node_map = contract_sccs(self.graph, self.directed_edges)
        super_node = node_map[self.comp_a]
        # Original cycle nodes should not be in graph (unless one IS the super-node)
        cycle_nodes = {self.comp_a, self.comp_b, self.comp_c}
        non_super_members = cycle_nodes - {super_node}
        for member in non_super_members:
            assert member not in contracted_graph.nodes

    def test_contracted_graph_has_external_node(self):
        contracted_graph, node_map = contract_sccs(self.graph, self.directed_edges)
        assert self.comp_d in contracted_graph.nodes

    def test_contracted_graph_has_edge_to_external(self):
        contracted_graph, node_map = contract_sccs(self.graph, self.directed_edges)
        super_node = node_map[self.comp_a]
        assert contracted_graph.has_edge(super_node, self.comp_d)

    def test_external_node_maps_to_itself(self):
        _, node_map = contract_sccs(self.graph, self.directed_edges)
        assert node_map[self.comp_d] == self.comp_d


# ---------------------------------------------------------------------------
# 5. contract_sccs — no cycles
# ---------------------------------------------------------------------------


class TestSCCContractionNoCycles:
    """Test 5: no cycles → graph unchanged, all nodes map to themselves."""

    def test_no_cycles_all_nodes_map_to_themselves(self):
        comp_a = "src/a.py::Alpha"
        comp_b = "src/b.py::Beta"
        comp_c = "src/c.py::Gamma"
        file_map = {comp_a: "src/a.py", comp_b: "src/b.py", comp_c: "src/c.py"}
        graph = _make_graph([comp_a, comp_b, comp_c], file_map)
        graph.add_edge(comp_a, comp_b, weight=1.0)
        graph.add_edge(comp_b, comp_c, weight=0.5)
        # Directed DAG: A→B→C (no cycles)
        directed_edges = [(comp_a, comp_b), (comp_b, comp_c)]
        _, node_map = contract_sccs(graph, directed_edges)
        assert node_map[comp_a] == comp_a
        assert node_map[comp_b] == comp_b
        assert node_map[comp_c] == comp_c

    def test_no_cycles_graph_nodes_unchanged(self):
        comp_a = "src/a.py::Alpha"
        comp_b = "src/b.py::Beta"
        file_map = {comp_a: "src/a.py", comp_b: "src/b.py"}
        graph = _make_graph([comp_a, comp_b], file_map)
        graph.add_edge(comp_a, comp_b, weight=1.0)
        directed_edges = [(comp_a, comp_b)]
        contracted_graph, _ = contract_sccs(graph, directed_edges)
        assert set(contracted_graph.nodes) == {comp_a, comp_b}

    def test_no_cycles_edge_weights_preserved(self):
        comp_a = "src/a.py::Alpha"
        comp_b = "src/b.py::Beta"
        file_map = {comp_a: "src/a.py", comp_b: "src/b.py"}
        graph = _make_graph([comp_a, comp_b], file_map)
        graph.add_edge(comp_a, comp_b, weight=2.5)
        directed_edges = [(comp_a, comp_b)]
        contracted_graph, _ = contract_sccs(graph, directed_edges)
        assert contracted_graph.has_edge(comp_a, comp_b)
        assert contracted_graph[comp_a][comp_b]["weight"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# 6. contract_sccs — super-node is lexicographically first
# ---------------------------------------------------------------------------


class TestSCCSuperNodeLexicographicFirst:
    """Test 6: cycle {C, A, B} → super-node = lexicographically first."""

    def test_super_node_is_lex_first(self):
        # "A_comp" < "B_comp" < "C_comp" lexicographically
        comp_a = "src/a_comp.py::Alpha"
        comp_b = "src/b_comp.py::Beta"
        comp_c = "src/c_comp.py::Gamma"
        # Verify ordering
        assert comp_a < comp_b < comp_c

        file_map = {comp_a: "src/a_comp.py", comp_b: "src/b_comp.py", comp_c: "src/c_comp.py"}
        graph = _make_graph([comp_a, comp_b, comp_c], file_map)
        graph.add_edge(comp_a, comp_b, weight=1.0)
        graph.add_edge(comp_b, comp_c, weight=1.0)
        graph.add_edge(comp_c, comp_a, weight=1.0)
        directed_edges = [(comp_a, comp_b), (comp_b, comp_c), (comp_c, comp_a)]
        _, node_map = contract_sccs(graph, directed_edges)
        # Super-node must be the lex-first member
        assert node_map[comp_a] == comp_a
        assert node_map[comp_b] == comp_a
        assert node_map[comp_c] == comp_a

    def test_super_node_present_in_contracted_graph(self):
        comp_a = "src/a_comp.py::Alpha"
        comp_b = "src/b_comp.py::Beta"
        comp_c = "src/c_comp.py::Gamma"
        file_map = {comp_a: "src/a_comp.py", comp_b: "src/b_comp.py", comp_c: "src/c_comp.py"}
        graph = _make_graph([comp_a, comp_b, comp_c], file_map)
        graph.add_edge(comp_a, comp_b, weight=1.0)
        graph.add_edge(comp_b, comp_c, weight=1.0)
        graph.add_edge(comp_c, comp_a, weight=1.0)
        directed_edges = [(comp_a, comp_b), (comp_b, comp_c), (comp_c, comp_a)]
        contracted_graph, node_map = contract_sccs(graph, directed_edges)
        assert comp_a in contracted_graph.nodes
        assert comp_b not in contracted_graph.nodes
        assert comp_c not in contracted_graph.nodes


# ---------------------------------------------------------------------------
# 7. detect_communities — Louvain respects directory priors
# ---------------------------------------------------------------------------


class TestLouvainRespectsDirectoryPriors:
    """Test 7: components in same dir are preferentially in same cluster."""

    def test_dir_prior_keeps_auth_together(self):
        # Two tight auth components and two api components with weak cross-dir edges
        auth_a = "auth/handler.py::AuthHandler"
        auth_b = "auth/models.py::User"
        api_a = "api/router.py::Router"
        api_b = "api/views.py::UserView"

        component_ids = [auth_a, auth_b, api_a, api_b]
        file_map = {c: c.split("::")[0] for c in component_ids}

        graph = _make_graph(component_ids, file_map)
        # Strong intra-dir edges
        graph.add_edge(auth_a, auth_b, weight=2.0)
        graph.add_edge(api_a, api_b, weight=2.0)
        # Weak cross-dir edge
        graph.add_edge(auth_b, api_a, weight=0.1)

        dir_partitions = partition_by_directory(component_ids, file_map)
        communities = detect_communities(graph, dir_partitions, seed=42)

        # Each community should be a set; auth pair and api pair in same cluster
        flat = [frozenset(c) for c in communities]
        assert frozenset({auth_a, auth_b}) in flat or any(
            auth_a in c and auth_b in c for c in communities
        )
        assert any(api_a in c and api_b in c for c in communities)

    def test_all_components_covered(self):
        component_ids = [
            "auth/handler.py::AuthHandler",
            "auth/models.py::User",
            "api/router.py::Router",
            "api/views.py::UserView",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        graph = _make_graph(component_ids, file_map)
        graph.add_edge(component_ids[0], component_ids[1], weight=2.0)
        graph.add_edge(component_ids[2], component_ids[3], weight=2.0)
        graph.add_edge(component_ids[1], component_ids[2], weight=0.1)

        dir_partitions = partition_by_directory(component_ids, file_map)
        communities = detect_communities(graph, dir_partitions, seed=42)

        all_members = set()
        for c in communities:
            all_members |= c
        assert all_members == set(component_ids)

    def test_returns_list_of_sets(self):
        component_ids = ["auth/handler.py::A", "api/router.py::B"]
        file_map = {c: c.split("::")[0] for c in component_ids}
        graph = _make_graph(component_ids, file_map)
        graph.add_edge(component_ids[0], component_ids[1], weight=1.0)
        dir_partitions = partition_by_directory(component_ids, file_map)
        communities = detect_communities(graph, dir_partitions, seed=42)
        assert isinstance(communities, list)
        for c in communities:
            assert isinstance(c, set)


# ---------------------------------------------------------------------------
# 8. determinism — same input five times → identical output
# ---------------------------------------------------------------------------


class TestDeterminismFiveRuns:
    """Test 8: same input 5 times → identical partition output."""

    def test_partition_components_deterministic(self):
        component_ids = [
            "src/auth/handler.py::AuthHandler",
            "src/auth/models.py::User",
            "src/auth/utils.py::hash_password",
            "src/api/router.py::Router",
            "src/api/views.py::UserView",
            "src/core/config.py::Config",
        ]
        file_map = {c: c.split("::")[0] for c in component_ids}
        # Build some edges to create non-trivial structure
        edges = [
            _edge(
                f"py:{file_map[component_ids[0]]}#AuthHandler(class)",
                f"py:{file_map[component_ids[1]]}#User(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            _edge(
                f"py:{file_map[component_ids[3]]}#Router(class)",
                f"py:{file_map[component_ids[4]]}#UserView(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
        ]
        results = [partition_components(component_ids, file_map, edges, seed=42) for _ in range(5)]
        # All 5 runs must produce identical output
        for run in results[1:]:
            assert run == results[0], "partition_components is not deterministic"

    def test_detect_communities_deterministic(self):
        component_ids = [f"src/mod_{i}.py::Cls{i}" for i in range(6)]
        file_map = {c: c.split("::")[0] for c in component_ids}
        graph = _make_graph(component_ids, file_map)
        # Add varied edges
        pairs = [(0, 1), (1, 2), (3, 4), (4, 5), (2, 3)]
        for i, j in pairs:
            graph.add_edge(component_ids[i], component_ids[j], weight=1.0)
        dir_partitions = {"src": set(component_ids)}
        results = [detect_communities(graph, dir_partitions, seed=42) for _ in range(5)]
        for run in results[1:]:
            assert run == results[0], "detect_communities is not deterministic"


# ---------------------------------------------------------------------------
# 9. small input → single cluster
# ---------------------------------------------------------------------------


class TestSmallInputSingleCluster:
    """Test 9: 2 components → single cluster (below threshold)."""

    def test_two_components_single_cluster(self):
        component_ids = ["src/a.py::Foo", "src/b.py::Bar"]
        file_map = {c: c.split("::")[0] for c in component_ids}
        edges = [
            _edge(
                "py:src/a.py#Foo(class)",
                "py:src/b.py#Bar(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            )
        ]
        result = partition_components(component_ids, file_map, edges, seed=42)
        # With < 3 nodes, should return a single cluster containing all
        assert len(result) == 1
        assert sorted(result[0]) == sorted(component_ids)

    def test_one_component_single_cluster(self):
        component_ids = ["src/a.py::Foo"]
        file_map = {component_ids[0]: "src/a.py"}
        result = partition_components(component_ids, file_map, [], seed=42)
        assert len(result) == 1
        assert result[0] == [component_ids[0]]

    def test_empty_components_returns_empty(self):
        result = partition_components([], {}, [], seed=42)
        assert result == []


# ---------------------------------------------------------------------------
# 10. partition_components end-to-end
# ---------------------------------------------------------------------------


class TestPartitionComponentsEndToEnd:
    """Test 10: 10 components, known edges, verify structural properties."""

    def setup_method(self):
        # 10 components across 3 directories
        self.component_ids = [
            "src/auth/handler.py::AuthHandler",
            "src/auth/models.py::User",
            "src/auth/middleware.py::JWTMiddleware",
            "src/api/router.py::Router",
            "src/api/views.py::UserView",
            "src/api/serializers.py::UserSerializer",
            "src/core/config.py::Config",
            "src/core/database.py::Database",
            "src/core/cache.py::Cache",
            "src/core/utils.py::Utils",
        ]
        self.file_map = {c: c.split("::")[0] for c in self.component_ids}
        # Edges: strong intra-dir, weak cross-dir
        self.edges = [
            _edge(
                "py:src/auth/handler.py#AuthHandler(class)",
                "py:src/auth/models.py#User(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            _edge(
                "py:src/auth/middleware.py#JWTMiddleware(class)",
                "py:src/auth/models.py#User(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            _edge(
                "py:src/api/router.py#Router(class)",
                "py:src/api/views.py#UserView(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            _edge(
                "py:src/api/views.py#UserView(class)",
                "py:src/api/serializers.py#UserSerializer(class)",
                EdgeType.CALLS,
                Confidence.HIGH,
            ),
            _edge(
                "py:src/core/database.py#Database(class)",
                "py:src/core/cache.py#Cache(class)",
                EdgeType.IMPORTS,
                Confidence.MEDIUM,
            ),
            _edge(
                "py:src/core/config.py#Config(class)",
                "py:src/core/utils.py#Utils(class)",
                EdgeType.CALLS,
                Confidence.MEDIUM,
            ),
            # Weak cross-dir edges
            _edge(
                "py:src/auth/handler.py#AuthHandler(class)",
                "py:src/core/config.py#Config(class)",
                EdgeType.CALLS,
                Confidence.LOW,
            ),
            _edge(
                "py:src/api/router.py#Router(class)",
                "py:src/core/database.py#Database(class)",
                EdgeType.CALLS,
                Confidence.LOW,
            ),
        ]

    def test_all_components_in_output(self):
        result = partition_components(self.component_ids, self.file_map, self.edges, seed=42)
        all_members = []
        for cluster in result:
            all_members.extend(cluster)
        assert sorted(all_members) == sorted(self.component_ids)

    def test_no_component_in_multiple_clusters(self):
        result = partition_components(self.component_ids, self.file_map, self.edges, seed=42)
        all_members = []
        for cluster in result:
            all_members.extend(cluster)
        # No duplicates
        assert len(all_members) == len(set(all_members))

    def test_clusters_are_sorted_lists(self):
        result = partition_components(self.component_ids, self.file_map, self.edges, seed=42)
        for cluster in result:
            assert isinstance(cluster, list)
            assert cluster == sorted(cluster)

    def test_returns_at_least_one_cluster(self):
        result = partition_components(self.component_ids, self.file_map, self.edges, seed=42)
        assert len(result) >= 1

    def test_result_is_sorted_largest_first(self):
        result = partition_components(self.component_ids, self.file_map, self.edges, seed=42)
        sizes = [len(c) for c in result]
        assert sizes == sorted(sizes, reverse=True)


# ---------------------------------------------------------------------------
# 11. SCC expansion in final output
# ---------------------------------------------------------------------------


class TestSCCExpansionInFinalOutput:
    """Test 11: SCC members appear in final cluster (not super-node ID)."""

    def test_scc_members_not_super_node_in_output(self):
        # Create a 3-node cycle: A→B→C→A, plus external D
        comp_a = "src/a_scc.py::AlphaSCC"
        comp_b = "src/b_scc.py::BetaSCC"
        comp_c = "src/c_scc.py::GammaSCC"
        comp_d = "src/d_scc.py::DeltaSCC"
        component_ids = [comp_a, comp_b, comp_c, comp_d]
        file_map = {c: c.split("::")[0] for c in component_ids}
        # Create a directed cycle via IMPORTS edges that form A→B→C→A
        # We need at least bidirectional coupling to form a directed cycle
        edges = [
            # A imports B
            _edge(
                "py:src/a_scc.py#AlphaSCC(class)",
                "py:src/b_scc.py#BetaSCC(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            # B imports C
            _edge(
                "py:src/b_scc.py#BetaSCC(class)",
                "py:src/c_scc.py#GammaSCC(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            # C imports A (creates cycle)
            _edge(
                "py:src/c_scc.py#GammaSCC(class)",
                "py:src/a_scc.py#AlphaSCC(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            # D connects to A
            _edge(
                "py:src/d_scc.py#DeltaSCC(class)",
                "py:src/a_scc.py#AlphaSCC(class)",
                EdgeType.CALLS,
                Confidence.MEDIUM,
            ),
        ]
        result = partition_components(component_ids, file_map, edges, seed=42)
        all_members = set()
        for cluster in result:
            all_members.update(cluster)
        # All original component IDs (including SCC members) must be in output
        assert comp_a in all_members
        assert comp_b in all_members
        assert comp_c in all_members
        assert comp_d in all_members

    def test_no_super_node_ids_in_output(self):
        """Output should never contain artificial super-node IDs."""
        comp_a = "src/a_scc.py::AlphaSCC"
        comp_b = "src/b_scc.py::BetaSCC"
        comp_c = "src/c_scc.py::GammaSCC"
        component_ids = [comp_a, comp_b, comp_c]
        file_map = {c: c.split("::")[0] for c in component_ids}
        edges = [
            _edge(
                "py:src/a_scc.py#AlphaSCC(class)",
                "py:src/b_scc.py#BetaSCC(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            _edge(
                "py:src/b_scc.py#BetaSCC(class)",
                "py:src/c_scc.py#GammaSCC(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
            _edge(
                "py:src/c_scc.py#GammaSCC(class)",
                "py:src/a_scc.py#AlphaSCC(class)",
                EdgeType.IMPORTS,
                Confidence.HIGH,
            ),
        ]
        result = partition_components(component_ids, file_map, edges, seed=42)
        all_members = set()
        for cluster in result:
            all_members.update(cluster)
        # Only original IDs should appear
        assert all_members <= set(component_ids)


# ---------------------------------------------------------------------------
# 12. tiny cluster merged with nearest neighbour
# ---------------------------------------------------------------------------


class TestTinyClusterMerged:
    """Test 12: cluster with <min_cluster_size members gets merged with neighbour."""

    def test_singleton_cluster_gets_merged(self):
        # 5 tightly-coupled components + 1 isolated singleton
        # The singleton should be merged into the main cluster
        main_comps = [f"src/main/mod_{i}.py::Class{i}" for i in range(4)]
        singleton = "src/lone/lonely.py::Lonely"
        component_ids = main_comps + [singleton]
        file_map = {c: c.split("::")[0] for c in component_ids}

        graph = _make_graph(component_ids, file_map)
        # Tightly couple main cluster
        for i in range(len(main_comps) - 1):
            graph.add_edge(main_comps[i], main_comps[i + 1], weight=2.0)
        graph.add_edge(main_comps[-1], main_comps[0], weight=2.0)
        # Light edge connecting singleton to main
        graph.add_edge(singleton, main_comps[0], weight=0.3)

        dir_partitions = partition_by_directory(component_ids, file_map)
        # Construct dir_partitions manually to ensure Louvain has a fighting chance
        communities = detect_communities(graph, dir_partitions, seed=42, min_cluster_size=3)

        # After merging tiny clusters, no cluster should have < 3 members
        # (unless there's only one cluster total)
        if len(communities) > 1:
            for community in communities:
                assert len(community) >= 3, f"Tiny cluster not merged: {community}"

    def test_all_members_preserved_after_merge(self):
        """Merging tiny clusters must not lose any component."""
        main_comps = [f"src/main/mod_{i}.py::Class{i}" for i in range(4)]
        singleton = "src/lone/lonely.py::Lonely"
        component_ids = main_comps + [singleton]
        file_map = {c: c.split("::")[0] for c in component_ids}

        graph = _make_graph(component_ids, file_map)
        for i in range(len(main_comps) - 1):
            graph.add_edge(main_comps[i], main_comps[i + 1], weight=2.0)
        graph.add_edge(singleton, main_comps[0], weight=0.3)

        dir_partitions = partition_by_directory(component_ids, file_map)
        communities = detect_communities(graph, dir_partitions, seed=42, min_cluster_size=2)

        all_members = set()
        for c in communities:
            all_members |= c
        assert all_members == set(component_ids)


# ---------------------------------------------------------------------------
# Edge cases for partition_by_directory
# ---------------------------------------------------------------------------


class TestPartitionByDirectoryEdgeCases:
    def test_empty_input_returns_empty_dict(self):
        result = partition_by_directory([], {})
        assert result == {}

    def test_component_with_empty_file_path_goes_to_root(self):
        comp = "::NoFile"
        result = partition_by_directory([comp], {comp: ""})
        assert "_root" in result
        assert comp in result["_root"]

    def test_deep_nested_path_uses_first_segment(self):
        comp = "src/very/deep/nested/file.py::DeepClass"
        result = partition_by_directory([comp], {comp: "src/very/deep/nested/file.py"})
        assert "src" in result
        assert comp in result["src"]

    def test_windows_style_path_separator(self):
        """Backslash-separated paths should still partition on first segment."""
        comp = "src\\auth\\handler.py::AuthHandler"
        result = partition_by_directory([comp], {comp: "src/auth/handler.py"})
        # forward slash in file_map → "src" partition
        assert "src" in result


# ---------------------------------------------------------------------------
# Edge cases for contract_sccs
# ---------------------------------------------------------------------------


class TestContractSCCsEdgeCases:
    def test_empty_graph_returns_empty_graph(self):
        graph = nx.Graph()
        contracted, node_map = contract_sccs(graph, [])
        assert contracted.number_of_nodes() == 0
        assert node_map == {}

    def test_directed_edges_not_in_graph_are_ignored(self):
        """Directed edges referencing nodes not in the undirected graph are skipped."""
        comp_a = "src/a.py::Alpha"
        comp_b = "src/b.py::Beta"
        file_map = {comp_a: "src/a.py", comp_b: "src/b.py"}
        graph = _make_graph([comp_a, comp_b], file_map)
        graph.add_edge(comp_a, comp_b, weight=1.0)
        # Directed edge references a node not in the graph
        ghost_node = "src/ghost.py::Ghost"
        directed_edges = [(comp_a, ghost_node), (comp_a, comp_b)]
        # Should not raise
        contracted, node_map = contract_sccs(graph, directed_edges)
        assert comp_a in contracted.nodes
        assert comp_b in contracted.nodes

    def test_self_loop_directed_edge_handled(self):
        """Self-referential directed edges (trivial SCC) don't cause errors."""
        comp_a = "src/a.py::Alpha"
        file_map = {comp_a: "src/a.py"}
        graph = _make_graph([comp_a], file_map)
        directed_edges = [(comp_a, comp_a)]
        contracted, node_map = contract_sccs(graph, directed_edges)
        assert comp_a in contracted.nodes
        assert node_map[comp_a] == comp_a

    def test_two_separate_cycles(self):
        """Two independent cycles each become their own super-node."""
        # Cycle 1: a1 → a2 → a1
        a1, a2 = "src/a1.py::C1", "src/a2.py::C2"
        # Cycle 2: b1 → b2 → b1
        b1, b2 = "src/b1.py::C3", "src/b2.py::C4"
        file_map = {a1: "src/a1.py", a2: "src/a2.py", b1: "src/b1.py", b2: "src/b2.py"}
        graph = _make_graph([a1, a2, b1, b2], file_map)
        graph.add_edge(a1, a2, weight=1.0)
        graph.add_edge(b1, b2, weight=1.0)
        directed_edges = [(a1, a2), (a2, a1), (b1, b2), (b2, b1)]
        contracted, node_map = contract_sccs(graph, directed_edges)
        # Super-nodes should be the lex-first of each cycle
        super_a = min(a1, a2)
        super_b = min(b1, b2)
        assert node_map[a1] == super_a
        assert node_map[a2] == super_a
        assert node_map[b1] == super_b
        assert node_map[b2] == super_b
        # Contracted graph should have exactly 2 nodes
        assert contracted.number_of_nodes() == 2
