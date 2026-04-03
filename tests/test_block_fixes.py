import importlib
import inspect
import sys

import pytest


class TestB1NoStdoutReplacement:
    def test_str_replace_editor_does_not_replace_stdout(self):
        """Module must not replace sys.stdout at import time."""
        original = sys.stdout
        import codewiki.src.be.agent_tools.str_replace_editor as editor

        importlib.reload(editor)
        assert sys.stdout is original or type(sys.stdout).__name__ == type(original).__name__


class TestB2NoBareExcept:
    def test_cloning_no_bare_except(self):
        from codewiki.src.be.dependency_analyzer.analysis import cloning

        source = inspect.getsource(cloning)
        for i, line in enumerate(source.split("\n"), 1):
            stripped = line.strip()
            if stripped == "except:" or stripped == "except:  # noqa":
                pytest.fail(f"Bare except: at line {i}")

    def test_generate_no_bare_except(self):
        from codewiki.cli.commands import generate

        source = inspect.getsource(generate)
        for i, line in enumerate(source.split("\n"), 1):
            if line.strip() == "except:":
                pytest.fail(f"Bare except: at line {i}")


class TestB4TokenFieldAlignment:
    def test_doc_generator_reads_correct_token_fields(self):
        """CLI adapter must read total_input_tokens, not total_input."""
        from codewiki.cli.adapters import doc_generator

        source = inspect.getsource(doc_generator)
        assert "total_input_tokens" in source
        assert "total_output_tokens" in source
        assert 'get("total_input"' not in source
        assert 'get("total_output"' not in source
