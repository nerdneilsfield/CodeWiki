# tests/test_perf_python_analyzer.py
from codewiki.src.be.dependency_analyzer.analyzers.python import PythonASTAnalyzer

SAMPLE = '''
class Foo:
    def bar(self):
        self.baz()

def standalone():
    pass
'''


def test_cached_paths_set_on_init():
    """_relative_path and _module_path must be attributes after __init__."""
    a = PythonASTAnalyzer("/repo/pkg/mod.py", SAMPLE, repo_path="/repo")
    assert hasattr(a, "_relative_path"), "_relative_path not pre-computed"
    assert hasattr(a, "_module_path"), "_module_path not pre-computed"


def test_get_relative_path_returns_cached_value():
    """_get_relative_path must return the same object on repeated calls."""
    a = PythonASTAnalyzer("/repo/pkg/mod.py", SAMPLE, repo_path="/repo")
    r1 = a._get_relative_path()
    r2 = a._get_relative_path()
    assert r1 is r2, "Expected cached string, got different objects"
    assert r1 == "pkg/mod.py"


def test_get_module_path_returns_cached_value():
    """_get_module_path must return the same object on repeated calls."""
    a = PythonASTAnalyzer("/repo/pkg/mod.py", SAMPLE, repo_path="/repo")
    m1 = a._get_module_path()
    m2 = a._get_module_path()
    assert m1 is m2, "Expected cached string, got different objects"
    assert m1 == "pkg.mod"


def test_analysis_still_works_after_caching():
    """Full analysis must still produce correct results."""
    from codewiki.src.be.dependency_analyzer.analyzers.python import analyze_python_file
    nodes, rels = analyze_python_file("/repo/pkg/mod.py", SAMPLE, "/repo")
    assert any(n.name == "Foo" for n in nodes)
    assert any(n.name == "standalone" for n in nodes)
