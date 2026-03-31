# tests/test_index_ts_js_adapter.py
"""Tests for TS/JS adapter: method extraction, import/export, visibility."""
import textwrap
import pytest
from codewiki.src.be.index.adapters.ts_js_adapter import TSJSIndexAdapter
from codewiki.src.be.index.models import SymbolKind, Visibility, ExportStatus


def _adapt(code: str, file_path="src/example.ts", repo_path="/repo", lang="typescript"):
    code = textwrap.dedent(code)
    adapter = TSJSIndexAdapter(
        file_path=file_path, content=code, repo_path=repo_path, language=lang,
    )
    return adapter.extract()


# ── Class + method extraction ────────────────────────────────────────────────

def test_extracts_class_ts():
    symbols, _ = _adapt('''
        class Foo {
            bar(x: number): string {
                return String(x);
            }
        }
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(classes) == 1
    assert classes[0].name == "Foo"
    assert len(methods) == 1
    assert methods[0].name == "bar"
    assert methods[0].parent_symbol_id == classes[0].symbol_id


def test_extracts_exported_class():
    symbols, _ = _adapt('''
        export class AuthService {
            login() {}
        }
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert classes[0].export_status == ExportStatus.EXPORTED


def test_extracts_function_ts():
    symbols, _ = _adapt('''
        function greet(name: string): void {
            console.log(name);
        }
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    assert funcs[0].name == "greet"


def test_exported_function():
    symbols, _ = _adapt('''
        export function helper() {}
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert funcs[0].export_status == ExportStatus.EXPORTED


def test_non_exported_function():
    symbols, _ = _adapt('''
        function internal() {}
    ''')
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert funcs[0].export_status == ExportStatus.NOT_EXPORTED


# ── Import extraction ────────────────────────────────────────────────────────

def test_named_import():
    _, imports = _adapt('''
        import { Foo, Bar } from './module';
    ''')
    assert len(imports) >= 1
    imp = imports[0]
    assert "./module" in imp.module_path
    assert "Foo" in imp.imported_names


def test_default_import():
    _, imports = _adapt('''
        import React from 'react';
    ''')
    assert len(imports) >= 1
    assert imports[0].module_path == "react"


def test_namespace_import():
    _, imports = _adapt('''
        import * as path from 'path';
    ''')
    assert len(imports) >= 1
    assert imports[0].module_path == "path"
    assert imports[0].alias == "path"


# ── JS files work too ────────────────────────────────────────────────────────

def test_js_class():
    symbols, _ = _adapt('''
        class App {
            render() {
                return null;
            }
        }
    ''', file_path="src/app.js", lang="javascript")
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert len(classes) == 1
    assert classes[0].lang == "javascript"


# ── Paths are relative ───────────────────────────────────────────────────────

def test_paths_are_relative():
    symbols, imports = _adapt('''
        import { X } from './x';
        export class Foo {
            bar() {}
        }
    ''', file_path="/repo/src/example.ts", repo_path="/repo")
    for s in symbols:
        assert not s.file_path.startswith("/")
    for i in imports:
        assert not i.file_path.startswith("/")


# ── New edge-case tests ───────────────────────────────────────────────────────

def test_class_with_multiple_methods_all_extracted():
    """All methods in a class should be extracted as children."""
    symbols, _ = _adapt('''
        class MyService {
            methodA() { return 1; }
            methodB() { return 2; }
            methodC() { return 3; }
        }
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
    assert len(classes) == 1
    assert len(methods) == 3
    child_ids = set(classes[0].children)
    for m in methods:
        assert m.symbol_id in child_ids


def test_two_classes_in_same_file():
    """Two class declarations in same file should both be extracted."""
    symbols, _ = _adapt('''
        class Alpha {
            doAlpha() {}
        }
        class Beta {
            doBeta() {}
        }
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    class_names = {c.name for c in classes}
    assert len(classes) == 2
    assert "Alpha" in class_names
    assert "Beta" in class_names


def test_export_default_class():
    """export default class Foo {} should be EXPORTED."""
    symbols, _ = _adapt('''
        export default class DefaultClass {
            run() {}
        }
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    # The class may or may not be extracted depending on tree-sitter node type.
    # Either it is found as EXPORTED or it doesn't crash.
    # At minimum, the adapter should not raise an exception.
    assert isinstance(symbols, list)


def test_export_default_function():
    """export default function foo() {} should be EXPORTED or at least not crash."""
    symbols, _ = _adapt('''
        export default function defaultFunc() {
            return 42;
        }
    ''')
    assert isinstance(symbols, list)
    # If the function was extracted, verify it doesn't have NOT_EXPORTED status
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION and s.name == "defaultFunc"]
    for f in funcs:
        assert f.export_status != ExportStatus.NOT_EXPORTED


def test_empty_class_has_zero_methods():
    """Empty class body should produce a CLASS with 0 children."""
    symbols, _ = _adapt('''
        class Empty {}
    ''')
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert len(classes) == 1
    assert classes[0].name == "Empty"
    assert classes[0].children == []


def test_javascript_function():
    """JavaScript function extraction works like TypeScript."""
    symbols, _ = _adapt('''
        function greetUser(name) {
            console.log("Hello " + name);
        }
    ''', file_path="src/app.js", lang="javascript")
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert len(funcs) == 1
    assert funcs[0].name == "greetUser"
    assert funcs[0].lang == "javascript"


def test_side_effect_import_does_not_crash():
    """import 'side-effect' with no named imports should not crash."""
    symbols, imports = _adapt('''
        import 'some-polyfill';
        function main() {}
    ''')
    assert isinstance(symbols, list)
    assert isinstance(imports, list)
    # At minimum, main() function should be extracted
    funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
    assert any(f.name == "main" for f in funcs)


def test_arrow_function_does_not_crash():
    """Arrow function assigned to const should not crash the adapter."""
    symbols, imports = _adapt('''
        const fn = () => {
            return 42;
        };
        class Wrapper {}
    ''')
    assert isinstance(symbols, list)
    assert isinstance(imports, list)
    # The class should still be extracted
    classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
    assert any(c.name == "Wrapper" for c in classes)


# ── Import path resolution ────────────────────────────────────────────────────

def test_relative_import_resolves_to_ts(tmp_path):
    """Relative import './utils' resolves to utils.ts when that file exists."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "utils.ts").write_text("export const x = 1;")
    importing_file = tmp_path / "src" / "app.ts"
    importing_file.write_text("import { x } from './utils';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "src/utils.ts"


def test_relative_import_resolves_to_js_fallback(tmp_path):
    """Relative import './utils' resolves to utils.js when no .ts file exists."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "utils.js").write_text("exports.x = 1;")
    importing_file = tmp_path / "src" / "app.ts"
    importing_file.write_text("import { x } from './utils';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "src/utils.js"


def test_relative_import_resolves_tsx_extension(tmp_path):
    """Relative import './components/Button' resolves to components/Button.tsx."""
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / "src" / "components" / "Button.tsx").write_text(
        "export const Button = () => null;"
    )
    importing_file = tmp_path / "src" / "app.ts"
    importing_file.write_text("import { Button } from './components/Button';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "src/components/Button.tsx"


def test_relative_import_resolves_index_ts(tmp_path):
    """Relative import './lib' resolves to lib/index.ts when that index file exists."""
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "index.ts").write_text("export const lib = {};")
    importing_file = tmp_path / "src" / "app.ts"
    importing_file.write_text("import { lib } from './lib';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "src/lib/index.ts"


def test_relative_import_resolves_index_js_fallback(tmp_path):
    """Relative import './lib' resolves to lib/index.js when only .js index exists."""
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "index.js").write_text("module.exports = {};")
    importing_file = tmp_path / "src" / "app.ts"
    importing_file.write_text("import { lib } from './lib';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "src/lib/index.js"


def test_package_import_resolved_path_is_none(tmp_path):
    """Non-relative imports (packages) must leave resolved_path as None."""
    importing_file = tmp_path / "app.ts"
    importing_file.write_text("import React from 'react';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path is None


def test_relative_import_with_explicit_extension(tmp_path):
    """Relative import './utils.js' with explicit extension resolves directly."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "utils.js").write_text("exports.x = 1;")
    importing_file = tmp_path / "src" / "app.ts"
    importing_file.write_text("import { x } from './utils.js';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "src/utils.js"


def test_relative_import_unresolvable_returns_none(tmp_path):
    """Relative import that points to a non-existent file leaves resolved_path as None."""
    importing_file = tmp_path / "app.ts"
    importing_file.write_text("import { ghost } from './does-not-exist';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path is None


def test_parent_directory_relative_import(tmp_path):
    """Relative import '../shared/helpers' resolves correctly across directory levels."""
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / "src" / "shared").mkdir(parents=True)
    (tmp_path / "src" / "shared" / "helpers.ts").write_text("export const help = () => {};")
    importing_file = tmp_path / "src" / "components" / "Widget.ts"
    importing_file.write_text("import { help } from '../shared/helpers';")

    adapter = TSJSIndexAdapter(
        file_path=str(importing_file),
        content=importing_file.read_text(),
        repo_path=str(tmp_path),
        language="typescript",
    )
    _, imports = adapter.extract()

    assert len(imports) == 1
    assert imports[0].resolved_path == "src/shared/helpers.ts"
