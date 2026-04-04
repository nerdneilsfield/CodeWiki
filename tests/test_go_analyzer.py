from codewiki.src.be.dependency_analyzer.analyzers.go import analyze_go_file


GO_SAMPLE = """
package sample

type Service struct{}
type Runner interface {
    Run()
}

func helper(v int) int {
    return v
}

func (s *Service) Run(items []int) {
    helper(len(items))
}
""".strip()


def test_go_analyzer_extracts_types_functions_and_methods(tmp_path):
    nodes, rels = analyze_go_file(str(tmp_path / "pkg" / "service.go"), GO_SAMPLE, str(tmp_path))

    names = {node.name for node in nodes}
    component_types = {node.name: node.component_type for node in nodes}

    assert {"Service", "Runner", "helper", "Service.Run"} <= names
    assert component_types["Service"] == "struct"
    assert component_types["Runner"] == "interface"
    assert component_types["helper"] == "function"
    assert component_types["Service.Run"] == "method"


def test_go_analyzer_records_helper_call_and_ignores_builtin_len(tmp_path):
    _, rels = analyze_go_file(str(tmp_path / "pkg" / "service.go"), GO_SAMPLE, str(tmp_path))

    call_pairs = {(rel.caller, rel.callee) for rel in rels}
    assert ("pkg.service.Service.Run", "helper") in call_pairs
    assert all(rel.callee != "len" for rel in rels)
