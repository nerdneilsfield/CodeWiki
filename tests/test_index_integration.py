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
