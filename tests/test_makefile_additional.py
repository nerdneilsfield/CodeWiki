import pytest

pytest.importorskip(
    "tree_sitter_make", reason="tree-sitter-make not installed; pip install 'codewiki[make]'"
)

from codewiki.src.be.dependency_analyzer.analyzers.makefile import analyze_makefile_file


def test_makefile_marks_generic_prerequisites_as_prerequisite(tmp_path):
    content = """
app: VERSION.txt assets
\t@echo build

assets:
\t@echo assets
""".strip()

    _, rels = analyze_makefile_file(str(tmp_path / "Makefile"), content, str(tmp_path))

    rel_types = {(rel.callee, rel.relationship_type) for rel in rels}
    assert ("VERSION.txt", "prerequisite") in rel_types
    assert any(
        rel.relationship_type == "target_dep" and rel.callee.endswith(".assets") for rel in rels
    )


def test_makefile_module_and_relative_path_fallback_when_relpath_fails(tmp_path, monkeypatch):
    from codewiki.src.be.dependency_analyzer.analyzers.makefile import TreeSitterMakefileAnalyzer

    content = "all:\n\t@echo ok\n"

    def boom(*_args, **_kwargs):
        raise ValueError("bad relpath")

    monkeypatch.setattr(
        "codewiki.src.be.dependency_analyzer.analyzers.makefile.os.path.relpath", boom
    )

    analyzer = TreeSitterMakefileAnalyzer(
        str(tmp_path / "proj" / "Makefile"), content, str(tmp_path)
    )

    assert analyzer.nodes[0].relative_path.endswith("Makefile")
    assert "Makefile" in analyzer.nodes[0].id
