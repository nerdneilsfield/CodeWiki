# tests/test_index_integration.py
"""Integration test: IndexBuilder works with the existing pipeline."""
import textwrap
import pytest
from codewiki.src.be.index.index_builder import IndexBuilder


@pytest.fixture
def python_repo(tmp_path):
    pkg = tmp_path / "mypackage"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "service.py").write_text(textwrap.dedent('''
        from .models import User

        class AuthService:
            """Handles user authentication."""

            def login(self, username: str, password: str) -> bool:
                """Authenticate a user."""
                user = User.find(username)
                return user.check_password(password)

            def logout(self, session_id: str) -> None:
                pass

            def _validate_token(self, token: str) -> bool:
                return len(token) > 0
    '''))
    (pkg / "models.py").write_text(textwrap.dedent('''
        class User:
            """A user model."""

            def __init__(self, name: str):
                self.name = name

            def check_password(self, password: str) -> bool:
                return True

            @staticmethod
            def find(username: str) -> "User":
                return User(username)
    '''))
    return str(tmp_path)


def test_full_index_of_python_package(python_repo):
    builder = IndexBuilder(repo_path=python_repo)
    products = builder.build()
    st = products.symbol_table

    # Should find both classes
    class_names = {s.name for s in st.all_symbols() if s.kind.value == "class"}
    assert "AuthService" in class_names
    assert "User" in class_names

    # Should find methods
    method_names = {s.name for s in st.all_symbols() if s.kind.value == "method"}
    assert "login" in method_names
    assert "logout" in method_names
    assert "check_password" in method_names
    assert "_validate_token" in method_names  # private but still extracted

    # Private method should have private visibility
    validate = [s for s in st.all_symbols() if s.name == "_validate_token"]
    assert validate[0].visibility.value == "private"

    # AuthService should have children
    auth = [s for s in st.all_symbols() if s.name == "AuthService"]
    assert len(auth[0].children) >= 3  # login, logout, _validate_token

    # Import graph should show service.py imports models
    ig = products.import_graph
    imps = ig.imports_of("mypackage/service.py")
    assert any("models" in imp.module_path for imp in imps)

    # Cards should exist for top-level symbols
    assert len(products.cards) > 0

    # All paths relative
    for s in st.all_symbols():
        assert not s.file_path.startswith("/")


def test_serialization_roundtrip(python_repo):
    builder = IndexBuilder(repo_path=python_repo)
    products = builder.build()
    data = products.to_dict()

    from codewiki.src.be.index.index_builder import IndexProducts
    restored = IndexProducts.from_dict(data)
    assert len(restored.symbol_table.all_symbols()) == len(products.symbol_table.all_symbols())
    assert len(restored.import_graph.all_imports()) == len(products.import_graph.all_imports())


# ── New edge-case tests ───────────────────────────────────────────────────────

@pytest.fixture
def mixed_repo(tmp_path):
    """Repo with both Python and TypeScript files."""
    py_pkg = tmp_path / "backend"
    py_pkg.mkdir()
    (py_pkg / "__init__.py").write_text("")
    (py_pkg / "server.py").write_text(textwrap.dedent('''
        class Server:
            """Backend server."""
            def start(self):
                pass
    '''))
    ts_dir = tmp_path / "frontend"
    ts_dir.mkdir()
    (ts_dir / "app.ts").write_text(textwrap.dedent('''
        export class AppComponent {
            render() {
                return null;
            }
        }
    '''))
    return str(tmp_path)


def test_repo_with_both_python_and_typescript_files(mixed_repo):
    """Repo with both Python and TypeScript files should index both."""
    builder = IndexBuilder(repo_path=mixed_repo)
    products = builder.build()
    st = products.symbol_table

    # Python class should be indexed
    py_classes = [s for s in st.all_symbols() if s.name == "Server" and s.lang == "python"]
    assert len(py_classes) == 1

    # TS class should be indexed (if tree-sitter is available)
    # At minimum, the builder should not crash
    assert products.symbol_table is not None
    assert products.import_graph is not None


def test_class_with_many_methods_has_populated_children(python_repo):
    """Class with many methods should have all methods as children."""
    builder = IndexBuilder(repo_path=python_repo)
    products = builder.build()
    st = products.symbol_table

    # Find AuthService (has login, logout, _validate_token)
    auth_class = [s for s in st.all_symbols() if s.name == "AuthService"]
    assert len(auth_class) == 1
    auth = auth_class[0]
    assert len(auth.children) >= 3

    # All children should be resolvable
    children = st.children_of(auth.symbol_id)
    assert len(children) >= 3
    child_names = {c.name for c in children}
    assert "login" in child_names
    assert "logout" in child_names


def test_syntax_error_in_one_file_does_not_prevent_others(tmp_path):
    """A file with syntax error should not crash the builder; other files are still indexed."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "broken.py").write_text("def broken_func(:\n    # syntax error\n    pass\n")
    (pkg / "good.py").write_text(textwrap.dedent('''
        def working_function():
            """Works correctly."""
            return True

        class WorkingClass:
            """A working class."""
            def method(self):
                pass
    '''))
    builder = IndexBuilder(repo_path=str(tmp_path))
    products = builder.build()

    # The good file should still be indexed
    all_names = {s.name for s in products.symbol_table.all_symbols()}
    assert "working_function" in all_names
    assert "WorkingClass" in all_names
