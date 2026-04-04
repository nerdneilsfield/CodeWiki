from codewiki.src.be.dependency_analyzer.analyzers.bash import analyze_bash_file


def test_bash_analyzer_extracts_functions_and_call_relationships(tmp_path):
    content = """
foo() {
  bar
  echo "ok"
}

bar() {
  printf "done"
}
""".strip()

    nodes, calls = analyze_bash_file(
        str(tmp_path / "scripts" / "build.sh"),
        content,
        repo_path=str(tmp_path),
    )

    node_names = {node.name for node in nodes}
    assert node_names == {"foo", "bar"}

    assert len(calls) == 1
    rel = calls[0]
    assert rel.caller == "scripts.build.foo"
    assert rel.callee == "scripts.build.bar"
    assert rel.is_resolved is True


def test_bash_analyzer_ignores_builtin_commands(tmp_path):
    content = """
foo() {
  echo "ok"
  cd /tmp
}
""".strip()

    nodes, calls = analyze_bash_file(
        str(tmp_path / "scripts" / "build.sh"),
        content,
        repo_path=str(tmp_path),
    )

    assert [node.name for node in nodes] == ["foo"]
    assert calls == []
