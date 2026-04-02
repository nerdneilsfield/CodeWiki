# tests/test_index_python_adapter.py
"""Tests for Python adapter: method extraction, import extraction, visibility."""

import textwrap
import pytest
from codewiki.src.be.index.adapters.python_adapter import PythonIndexAdapter
from codewiki.src.be.index.models import SymbolKind, Visibility, ExportStatus


def _adapt(code: str, file_path="src/example.py", repo_path="/repo"):
    code = textwrap.dedent(code)
    adapter = PythonIndexAdapter(file_path=file_path, content=code, repo_path=repo_path)
    return adapter.extract()


# ── Class + method extraction ────────────────────────────────────────────────


def test_extracts_class():
    symbols, imports = _adapt('''
        class Foo:
            """A foo class."""
            pass
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert len(classes) == 1
    assert classes[0].name == "Foo"
    assert classes[0].docstring == "A foo class."


def test_extracts_methods_as_children():
    symbols, imports = _adapt('''
        class Foo:
            def bar(self, x: int) -> str:
                """Do bar."""
                return str(x)

            def baz(self):
                pass
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(classes) == 1
    assert len(methods) == 2
    # Methods are children of the class
    assert set(classes[0].children) == {m.symbol_id for m in methods}
    # Methods have parent_symbol_id
    for m in methods:
        assert m.parent_symbol_id == classes[0].symbol_id


def test_method_signature():
    symbols, _ = _adapt("""
        class Foo:
            def bar(self, x: int, y: str = "hi") -> bool:
                pass
    """)
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1
    assert "x: int" in methods[0].signature
    assert "-> bool" in methods[0].signature


def test_extracts_top_level_function():
    symbols, _ = _adapt('''
        def standalone(a, b):
            """A standalone function."""
            return a + b
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    assert funcs[0].name == "standalone"
    assert funcs[0].parent_symbol_id is None


def test_async_method():
    symbols, _ = _adapt("""
        class Service:
            async def fetch(self, url: str) -> bytes:
                pass
    """)
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1
    assert methods[0].name == "fetch"


def test_static_method():
    symbols, _ = _adapt("""
        class Util:
            @staticmethod
            def helper(x):
                pass
    """)
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1


# ── Import extraction ────────────────────────────────────────────────────────


def test_import_plain():
    _, imports = _adapt("""
        import os
        import sys
    """)
    assert len(imports) == 2
    names = {i.module_path for i in imports}
    assert "os" in names
    assert "sys" in names


def test_from_import():
    _, imports = _adapt("""
        from os.path import join, dirname
    """)
    assert len(imports) == 1
    assert imports[0].module_path == "os.path"
    assert imports[0].imported_names == ["join", "dirname"]


def test_import_alias():
    _, imports = _adapt("""
        import numpy as np
    """)
    assert imports[0].alias == "np"


def test_relative_import():
    _, imports = _adapt("""
        from ..utils import helper
    """)
    assert imports[0].module_path == "..utils"
    assert imports[0].imported_names == ["helper"]


def test_star_import():
    _, imports = _adapt("""
        from os.path import *
    """)
    assert imports[0].imported_names == ["*"]


# ── Visibility ───────────────────────────────────────────────────────────────


def test_private_function():
    symbols, _ = _adapt("""
        def _internal():
            pass
    """)
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert funcs[0].visibility == Visibility.PRIVATE


def test_dunder_private():
    symbols, _ = _adapt("""
        class Foo:
            def __secret(self):
                pass
    """)
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert methods[0].visibility == Visibility.PRIVATE


def test_public_by_default():
    symbols, _ = _adapt("""
        def public_func():
            pass
    """)
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert funcs[0].visibility == Visibility.PUBLIC


def test_export_from_all():
    symbols, _ = _adapt("""
        __all__ = ["exported_func"]

        def exported_func():
            pass

        def not_exported():
            pass
    """)
    exported = [s for s in symbols if s.export_status == ExportStatus.EXPORTED]
    not_exported = [s for s in symbols if s.export_status == ExportStatus.NOT_EXPORTED]
    assert len(exported) == 1
    assert exported[0].name == "exported_func"
    assert len(not_exported) >= 1


# ── File path is relative ────────────────────────────────────────────────────


def test_file_paths_are_relative():
    symbols, imports = _adapt(
        """
        import os

        class Foo:
            def bar(self):
                pass
    """,
        file_path="/repo/src/example.py",
        repo_path="/repo",
    )
    for s in symbols:
        assert not s.file_path.startswith("/"), (
            f"Symbol {s.symbol_id} has absolute path: {s.file_path}"
        )
    for i in imports:
        assert not i.file_path.startswith("/"), f"Import has absolute path: {i.file_path}"


# ── New edge-case tests ───────────────────────────────────────────────────────


def test_empty_file_returns_empty():
    symbols, imports = _adapt("")
    assert symbols == []
    assert imports == []


def test_file_with_only_comments_returns_empty():
    symbols, imports = _adapt("""
        # This is a comment
        # Another comment
        # No actual code here
    """)
    assert symbols == []
    assert imports == []


def test_syntax_error_returns_empty():
    symbols, imports = _adapt("""
        def broken(
            # unterminated function definition
        class Foo:
            pass
    """)
    assert symbols == []
    assert imports == []


def test_multiple_classes_in_one_file():
    symbols, _ = _adapt('''
        class Alpha:
            """First class."""
            pass

        class Beta:
            """Second class."""
            pass

        class Gamma:
            pass
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    class_names = {c.name for c in classes}
    assert len(classes) == 3
    assert class_names == {"Alpha", "Beta", "Gamma"}


def test_class_inheriting_from_base():
    symbols, _ = _adapt("""
        class Child(BaseClass):
            pass
    """)
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert len(classes) == 1
    assert "BaseClass" in classes[0].signature


def test_dunder_init_visibility():
    """__init__ starts with _ so it is treated as PRIVATE by the visibility logic."""
    symbols, _ = _adapt("""
        class Foo:
            def __init__(self, x: int):
                self.x = x
    """)
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1
    assert methods[0].name == "__init__"
    # The adapter treats any name starting with _ as PRIVATE (dunder methods included)
    assert methods[0].visibility == Visibility.PRIVATE


def test_dunder_str_visibility():
    """__str__ starts with _ so it is treated as PRIVATE by the visibility logic."""
    symbols, _ = _adapt("""
        class Foo:
            def __str__(self) -> str:
                return "Foo"
    """)
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1
    assert methods[0].name == "__str__"
    # Dunder methods are treated as PRIVATE because they start with underscore
    assert methods[0].visibility == Visibility.PRIVATE


def test_kwonly_args_in_signature():
    """Function with keyword-only args (after *) should still be extractable."""
    symbols, _ = _adapt("""
        def func_with_kwonly(a, b, *, key: str = "default", flag: bool = False):
            pass
    """)
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    assert funcs[0].name == "func_with_kwonly"
    # Should have extracted a signature
    assert funcs[0].signature is not None


def test_args_and_kwargs_in_signature():
    """Function with *args and **kwargs should be extractable."""
    symbols, _ = _adapt("""
        def variadic(*args, **kwargs):
            pass
    """)
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    assert "*args" in funcs[0].signature
    assert "**kwargs" in funcs[0].signature


def test_property_decorated_method():
    """@property decorator should still result in a METHOD symbol."""
    symbols, _ = _adapt("""
        class Foo:
            @property
            def value(self) -> int:
                return self._value
    """)
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(methods) == 1
    assert methods[0].name == "value"


def test_deep_module_path_in_qualified_name():
    """Deep file path like src/pkg/sub/module.py → qualified_name starts with src.pkg.sub.module."""
    symbols, _ = _adapt(
        """
        def my_func():
            pass
    """,
        file_path="src/pkg/sub/module.py",
        repo_path="",
    )
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    assert funcs[0].qualified_name.startswith("src.pkg.sub.module")


def test_dunder_all_as_tuple():
    """__all__ defined as a tuple should still determine export status."""
    symbols, _ = _adapt("""
        __all__ = ("exported_func",)

        def exported_func():
            pass

        def hidden_func():
            pass
    """)
    exported = [s for s in symbols if s.export_status == ExportStatus.EXPORTED]
    not_exported = [s for s in symbols if s.export_status == ExportStatus.NOT_EXPORTED]
    assert len(exported) == 1
    assert exported[0].name == "exported_func"
    assert any(s.name == "hidden_func" for s in not_exported)


def test_multiple_imports_from_same_module():
    """Multiple `from X import ...` statements from the same module create multiple ImportStatement objects."""
    _, imports = _adapt("""
        from os.path import join
        from os.path import dirname
    """)
    os_path_imports = [i for i in imports if i.module_path == "os.path"]
    assert len(os_path_imports) == 2


def test_relative_import_empty_module():
    """from . import foo (empty module in relative import) should work."""
    _, imports = _adapt("""
        from . import foo
    """)
    assert len(imports) == 1
    # module_path should just be "." (the dot prefix with empty module)
    assert imports[0].module_path == "."
    assert "foo" in imports[0].imported_names


# ── kwonlyargs signature tests ────────────────────────────────────────────────


def test_kwonly_bare_star_with_annotated_arg():
    """def f(a, *, key: str) → bare * separator + key: str in signature."""
    symbols, _ = _adapt("""
        def f(a, *, key: str):
            pass
    """)
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    sig = funcs[0].signature
    # bare * must appear before key
    assert "*, key: str" in sig, f"Expected '*, key: str' in signature, got: {sig!r}"


def test_kwonly_after_vararg_no_extra_star():
    """def f(a, *args, key: str = 'x') → *args present, so no extra bare *.
    The kwonly arg and its default must appear in the signature."""
    symbols, _ = _adapt("""
        def f(a, *args, key: str = 'x'):
            pass
    """)
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    sig = funcs[0].signature
    # *args comes first; no duplicate bare * should appear
    assert "*args" in sig, f"Expected '*args' in signature, got: {sig!r}"
    assert "key: str = 'x'" in sig, f"Expected \"key: str = 'x'\" in signature, got: {sig!r}"
    assert sig.count("*,") == 0, (
        f"Unexpected bare '*, ' in signature when *args is present: {sig!r}"
    )


def test_kwonly_multiple_args_only_bare_star():
    """def f(*, key1: int, key2: str = 'y') → bare * + both kwonly args in order."""
    symbols, _ = _adapt("""
        def f(*, key1: int, key2: str = 'y'):
            pass
    """)
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    sig = funcs[0].signature
    assert "*, key1: int, key2: str = 'y'" in sig, (
        f"Expected '*, key1: int, key2: str = \\'y\\'' in signature, got: {sig!r}"
    )


# ── Import path resolution ────────────────────────────────────────────────────


def test_relative_import_resolves_to_sibling_py_file(tmp_path):
    """from .utils import X in src/pkg/main.py resolves to src/pkg/utils.py"""
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "utils.py").write_text("def helper(): pass\n")
    main_file = pkg / "main.py"
    main_file.write_text("from .utils import helper\n")

    adapter = PythonIndexAdapter(
        file_path=str(main_file),
        content="from .utils import helper\n",
        repo_path=str(tmp_path),
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].module_path == ".utils"
    assert imports[0].resolved_path == "src/pkg/utils.py"


def test_relative_import_level2_resolves_correctly(tmp_path):
    """from ..utils import X in src/pkg/sub/mod.py resolves to src/pkg/utils.py"""
    pkg = tmp_path / "src" / "pkg"
    sub = pkg / "sub"
    sub.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (sub / "__init__.py").write_text("")
    (pkg / "utils.py").write_text("def helper(): pass\n")
    mod_file = sub / "mod.py"
    mod_file.write_text("from ..utils import helper\n")

    adapter = PythonIndexAdapter(
        file_path=str(mod_file),
        content="from ..utils import helper\n",
        repo_path=str(tmp_path),
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].module_path == "..utils"
    assert imports[0].resolved_path == "src/pkg/utils.py"


def test_absolute_import_resolves_when_file_exists(tmp_path):
    """from pkg.utils import X resolves to pkg/utils.py when that file exists"""
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "utils.py").write_text("X = 1\n")
    caller = tmp_path / "main.py"
    caller.write_text("from pkg.utils import X\n")

    adapter = PythonIndexAdapter(
        file_path=str(caller),
        content="from pkg.utils import X\n",
        repo_path=str(tmp_path),
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "pkg/utils.py"


def test_external_package_import_resolves_to_none(tmp_path):
    """from os.path import join resolves to None (not present under repo root)"""
    caller = tmp_path / "main.py"
    caller.write_text("from os.path import join\n")

    adapter = PythonIndexAdapter(
        file_path=str(caller),
        content="from os.path import join\n",
        repo_path=str(tmp_path),
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path is None


def test_relative_import_resolves_to_package_init(tmp_path):
    """from .subpkg import X resolves to src/pkg/subpkg/__init__.py"""
    pkg = tmp_path / "src" / "pkg"
    subpkg = pkg / "subpkg"
    subpkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (subpkg / "__init__.py").write_text("X = 42\n")
    main_file = pkg / "main.py"
    main_file.write_text("from .subpkg import X\n")

    adapter = PythonIndexAdapter(
        file_path=str(main_file),
        content="from .subpkg import X\n",
        repo_path=str(tmp_path),
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "src/pkg/subpkg/__init__.py"


def test_plain_import_resolves_when_file_exists(tmp_path):
    """import mymodule resolves to mymodule.py when that file is at repo root"""
    (tmp_path / "mymodule.py").write_text("pass\n")
    caller = tmp_path / "main.py"
    caller.write_text("import mymodule\n")

    adapter = PythonIndexAdapter(
        file_path=str(caller),
        content="import mymodule\n",
        repo_path=str(tmp_path),
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "mymodule.py"


def test_plain_import_external_resolves_to_none(tmp_path):
    """import os resolves to None (standard library, not in repo)"""
    caller = tmp_path / "main.py"
    caller.write_text("import os\n")

    adapter = PythonIndexAdapter(
        file_path=str(caller),
        content="import os\n",
        repo_path=str(tmp_path),
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path is None


def test_resolved_path_uses_forward_slashes(tmp_path):
    """resolved_path must always use forward slashes, never backslashes"""
    a = tmp_path / "a"
    b = a / "b"
    b.mkdir(parents=True)
    (a / "__init__.py").write_text("")
    (b / "__init__.py").write_text("")
    (a / "target.py").write_text("x = 1\n")
    caller = b / "caller.py"
    caller.write_text("from ..target import x\n")

    adapter = PythonIndexAdapter(
        file_path=str(caller),
        content="from ..target import x\n",
        repo_path=str(tmp_path),
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path is not None
    assert "\\" not in imports[0].resolved_path
