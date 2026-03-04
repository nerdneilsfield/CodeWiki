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
