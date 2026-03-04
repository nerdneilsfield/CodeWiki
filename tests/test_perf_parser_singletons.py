"""Verify that tree-sitter Language objects are module-level singletons."""


def test_c_language_is_singleton():
    from codewiki.src.be.dependency_analyzer.analyzers import c as c_mod
    from codewiki.src.be.dependency_analyzer.analyzers.c import analyze_c_file

    SAMPLE = "void foo(void) {}\nvoid bar(void) { foo(); }\n"
    analyze_c_file("/tmp/a.c", SAMPLE, "/tmp")
    analyze_c_file("/tmp/b.c", SAMPLE, "/tmp")

    assert c_mod._C_LANGUAGE is not None, "_C_LANGUAGE singleton not created"
    lang1 = c_mod._C_LANGUAGE
    analyze_c_file("/tmp/c.c", SAMPLE, "/tmp")
    lang2 = c_mod._C_LANGUAGE
    assert lang1 is lang2, "Language object was recreated between calls"


def test_cpp_language_is_singleton():
    from codewiki.src.be.dependency_analyzer.analyzers import cpp as cpp_mod
    from codewiki.src.be.dependency_analyzer.analyzers.cpp import analyze_cpp_file

    SAMPLE = "void foo() {}\nvoid bar() { foo(); }\n"
    analyze_cpp_file("/tmp/a.cpp", SAMPLE, "/tmp")
    lang1 = cpp_mod._CPP_LANGUAGE
    analyze_cpp_file("/tmp/b.cpp", SAMPLE, "/tmp")
    lang2 = cpp_mod._CPP_LANGUAGE
    assert lang1 is lang2


def test_js_language_is_singleton():
    from codewiki.src.be.dependency_analyzer.analyzers import javascript as js_mod
    from codewiki.src.be.dependency_analyzer.analyzers.javascript import analyze_javascript_file_treesitter

    SAMPLE = "function foo() {}\nfunction bar() { foo(); }\n"
    analyze_javascript_file_treesitter("/tmp/a.js", SAMPLE, "/tmp")
    lang1 = js_mod._JS_LANGUAGE
    analyze_javascript_file_treesitter("/tmp/b.js", SAMPLE, "/tmp")
    lang2 = js_mod._JS_LANGUAGE
    assert lang1 is lang2
