import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from codewiki.src.be.cache_manager import CacheManager


def test_load_hash_cache_returns_empty_on_invalid_json(tmp_path):
    from codewiki.src.be.docs_fixer import _load_hash_cache

    (tmp_path / ".fix_docs_cache.json").write_text("{bad json", encoding="utf-8")

    assert _load_hash_cache(tmp_path) == {}


def test_save_hash_cache_ignores_write_failures(tmp_path):
    from codewiki.src.be.docs_fixer import _save_hash_cache

    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        _save_hash_cache(tmp_path, {"overview.md": "abc"})

    assert not (tmp_path / ".fix_docs_cache.json").exists()


def test_fix_math_in_text_degrades_when_repair_is_unchanged():
    from codewiki.src.be.docs_fixer import FixStats
    from codewiki.src.be.postprocess.math_validator import fix_math_in_text
    from codewiki.src.be.postprocess.lint_report import LintReport

    stats = FixStats()
    report = LintReport()
    config = MagicMock()
    config.postprocess = MagicMock()
    config.postprocess.repair_batch_size = 8
    config.postprocess.repair_model = ""
    config.postprocess.repair_fallback_1 = ""
    config.postprocess.repair_fallback_2 = ""
    config.postprocess.repair_max_retries = 1

    with (
        patch(
            "codewiki.src.be.postprocess.math_validator.repair_batch_sync",
            return_value={"math.md:1:0": r"\frac{1}{"},
        ),
        patch(
            "codewiki.src.be.postprocess.math_validator._validate_katex",
            return_value=None,
        ),
    ):
        result = fix_math_in_text(
            "Broken $$\\frac{1}{$$ formula",
            config,
            stats,
            report=report,
            filename="math.md",
        )

    assert "```latex" in result
    assert "math-error:" in result
    assert stats.math_invalid == 1
    assert stats.math_failed == 1
    assert report.math_failures[0]["file"] == "math.md"


def test_fix_mermaid_in_text_degrades_when_repair_is_unchanged():
    from codewiki.src.be.docs_fixer import FixStats
    from codewiki.src.be.postprocess.mermaid_validator import fix_mermaid_in_text
    from codewiki.src.be.postprocess.lint_report import LintReport

    stats = FixStats()
    report = LintReport()
    config = MagicMock()
    config.postprocess = MagicMock()
    config.postprocess.repair_batch_size = 8
    config.postprocess.repair_model = ""
    config.postprocess.repair_fallback_1 = ""
    config.postprocess.repair_fallback_2 = ""
    config.postprocess.repair_max_retries = 1
    text = '```mermaid\nA["it\'s bad"] --> B\n```'

    with (
        patch("codewiki.src.be.postprocess.mermaid_validator._find_mmdc", return_value=None),
        patch(
            "codewiki.src.be.postprocess.mermaid_validator.repair_batch_sync",
            return_value={"diagram.md:0": 'A["it\'s bad"] --> B'},
        ),
    ):
        result = fix_mermaid_in_text(
            text,
            config,
            stats,
            report=report,
            filename="diagram.md",
        )

    assert "[MERMAID DIAGRAM - RENDER FAILED]" in result
    assert "mermaid-error:" in result
    assert stats.diagrams_invalid == 1
    assert stats.diagrams_failed == 1
    assert report.mermaid_failures[0]["file"] == "diagram.md"


def test_fix_mermaid_in_text_uses_cached_repair_result(tmp_path):
    from codewiki.src.be.docs_fixer import FixStats
    from codewiki.src.be.postprocess.mermaid_validator import (
        _repair_cache_path,
        cleanup_mermaid,
        fix_mermaid_in_text,
    )

    stats = FixStats()
    config = MagicMock()
    config.postprocess = MagicMock()
    config.postprocess.repair_batch_size = 8
    config.postprocess.repair_model = ""
    config.postprocess.repair_fallback_1 = ""
    config.postprocess.repair_fallback_2 = ""
    config.postprocess.repair_max_retries = 1
    config.postprocess.degrade_mermaid = True
    cache_manager = CacheManager(str(tmp_path / ".codewiki"))
    text = '```mermaid\nA["it\'s bad"] --> B\n```'
    repair_id = "postprocess_repair:diagram.md:mermaid_0"
    block_hash = (
        __import__("hashlib").sha256(cleanup_mermaid('A["it\'s bad"] --> B').encode()).hexdigest()
    )
    cached_path = _repair_cache_path(str(tmp_path / ".codewiki"), repair_id)
    Path(cached_path).write_text('A["its bad"] --> B', encoding="utf-8")
    cache_manager.mark_done(
        repair_id,
        input_hash=block_hash,
        output_path=cached_path,
        output_file=Path(cached_path).name,
    )

    with (
        patch("codewiki.src.be.postprocess.mermaid_validator._find_mmdc", return_value=None),
        patch(
            "codewiki.src.be.postprocess.mermaid_validator.repair_batch_sync",
            side_effect=AssertionError("should not call repair"),
        ),
    ):
        result = fix_mermaid_in_text(
            text,
            config,
            stats,
            cache_manager=cache_manager,
            filename="diagram.md",
        )

    assert 'A["its bad"] --> B' in result


def test_fix_mermaid_in_text_falls_back_when_cached_file_missing(tmp_path):
    from codewiki.src.be.docs_fixer import FixStats
    from codewiki.src.be.postprocess.mermaid_validator import (
        cleanup_mermaid,
        fix_mermaid_in_text,
    )

    stats = FixStats()
    config = MagicMock()
    config.postprocess = MagicMock()
    config.postprocess.repair_batch_size = 8
    config.postprocess.repair_model = ""
    config.postprocess.repair_fallback_1 = ""
    config.postprocess.repair_fallback_2 = ""
    config.postprocess.repair_max_retries = 1
    config.postprocess.degrade_mermaid = True
    cache_manager = CacheManager(str(tmp_path / ".codewiki"))
    text = '```mermaid\nA["it\'s bad"] --> B\n```'
    repair_id = "postprocess_repair:diagram.md:mermaid_0"
    block_hash = (
        __import__("hashlib").sha256(cleanup_mermaid('A["it\'s bad"] --> B').encode()).hexdigest()
    )
    cache_manager.mark_done(
        repair_id,
        input_hash=block_hash,
        output_path=str(tmp_path / ".codewiki" / "_repair_cache" / "missing.txt"),
        output_file="missing.txt",
    )

    with (
        patch("codewiki.src.be.postprocess.mermaid_validator._find_mmdc", return_value=None),
        patch(
            "codewiki.src.be.postprocess.mermaid_validator.repair_batch_sync",
            return_value={"diagram.md:0": 'A["its bad"] --> B'},
        ) as repair_batch,
    ):
        result = fix_mermaid_in_text(
            text,
            config,
            stats,
            cache_manager=cache_manager,
            filename="diagram.md",
        )

    repair_batch.assert_called_once()
    assert 'A["its bad"] --> B' in result


def test_fix_math_in_text_uses_cached_repair_result(tmp_path):
    from codewiki.src.be.docs_fixer import FixStats
    from codewiki.src.be.postprocess.math_validator import (
        _repair_cache_path,
        cleanup_formula,
        fix_math_in_text,
    )

    stats = FixStats()
    config = MagicMock()
    config.postprocess = MagicMock()
    config.postprocess.repair_batch_size = 8
    config.postprocess.repair_model = ""
    config.postprocess.repair_fallback_1 = ""
    config.postprocess.repair_fallback_2 = ""
    config.postprocess.repair_max_retries = 1
    cache_manager = CacheManager(str(tmp_path / ".codewiki"))
    text = "Broken $$\\frac{1}{$$ formula"
    repair_id = "postprocess_repair:math.md:math_1_0"
    formula_hash = __import__("hashlib").sha256(cleanup_formula(r"\frac{1}{").encode()).hexdigest()
    cached_path = _repair_cache_path(str(tmp_path / ".codewiki"), repair_id)
    Path(cached_path).write_text(r"\frac{1}{2}", encoding="utf-8")
    cache_manager.mark_done(
        repair_id,
        input_hash=formula_hash,
        output_path=cached_path,
        output_file=Path(cached_path).name,
    )

    with (
        patch(
            "codewiki.src.be.postprocess.math_validator.repair_batch_sync",
            side_effect=AssertionError("should not call repair"),
        ),
        patch("codewiki.src.be.postprocess.math_validator._validate_katex", return_value=None),
    ):
        result = fix_math_in_text(
            text,
            config,
            stats,
            cache_manager=cache_manager,
            filename="math.md",
        )

    assert r"$$\frac{1}{2}$$" in result


def test_fix_math_in_text_falls_back_when_cached_hash_is_stale(tmp_path):
    from codewiki.src.be.docs_fixer import FixStats
    from codewiki.src.be.postprocess.math_validator import fix_math_in_text

    stats = FixStats()
    config = MagicMock()
    config.postprocess = MagicMock()
    config.postprocess.repair_batch_size = 8
    config.postprocess.repair_model = ""
    config.postprocess.repair_fallback_1 = ""
    config.postprocess.repair_fallback_2 = ""
    config.postprocess.repair_max_retries = 1
    cache_manager = CacheManager(str(tmp_path / ".codewiki"))
    text = "Broken $$\\frac{1}{$$ formula"
    cache_manager.mark_done(
        "postprocess_repair:math.md:math_1_0",
        input_hash="stale-hash",
        output_path=str(tmp_path / ".codewiki" / "_repair_cache" / "stale.txt"),
        output_file="stale.txt",
    )

    with (
        patch(
            "codewiki.src.be.postprocess.math_validator.repair_batch_sync",
            return_value={"math.md:1:0": r"\frac{1}{2}"},
        ) as repair_batch,
        patch("codewiki.src.be.postprocess.math_validator._validate_katex", return_value=None),
    ):
        result = fix_math_in_text(
            text,
            config,
            stats,
            cache_manager=cache_manager,
            filename="math.md",
        )

    repair_batch.assert_called_once()
    assert r"$$\frac{1}{2}$$" in result


def test_fix_docs_strict_mode_raises_lint_error(tmp_path):
    from codewiki.src.be.docs_fixer import fix_docs
    from codewiki.src.be.postprocess.lint_report import LintError

    (tmp_path / "overview.md").write_text("Hello", encoding="utf-8")
    config = MagicMock()
    config.postprocess = MagicMock()
    config.postprocess.fix_links = False
    config.postprocess.strict = True

    with patch(
        "codewiki.src.be.docs_fixer.fix_mermaid",
        side_effect=lambda text, *args, report=None, filename="", **kwargs: (
            report.link_issues.append(
                {
                    "file": filename or "overview.md",
                    "line": 1,
                    "target": "missing.md",
                    "issue_type": "broken_link",
                }
            )
            or text
        ),
    ):
        try:
            fix_docs(str(tmp_path), config)
        except LintError as exc:
            payload = json.loads((tmp_path / "_lint_report.json").read_text(encoding="utf-8"))
            assert exc.report.has_failures is True
            assert payload["link_issues"][0]["target"] == "missing.md"
        else:
            raise AssertionError("Expected LintError in strict mode")


def test_validate_with_mmdc_times_out():
    from codewiki.src.be.postprocess.mermaid_validator import validate_with_mmdc

    with (
        patch(
            "codewiki.src.be.postprocess.mermaid_validator._find_mmdc", return_value="/usr/bin/mmdc"
        ),
        patch(
            "codewiki.src.be.postprocess.mermaid_validator.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["mmdc"], timeout=30),
        ),
    ):
        assert validate_with_mmdc("graph TD\nA-->B") == "mmdc timed out"


def test_validate_with_mmdc_returns_process_error_output():
    from codewiki.src.be.postprocess.mermaid_validator import validate_with_mmdc

    proc = MagicMock(returncode=1, stderr="bad syntax", stdout="")
    with (
        patch(
            "codewiki.src.be.postprocess.mermaid_validator._find_mmdc", return_value="/usr/bin/mmdc"
        ),
        patch("codewiki.src.be.postprocess.mermaid_validator.subprocess.run", return_value=proc),
    ):
        assert validate_with_mmdc("graph TD\nA-->B") == "bad syntax"


def test_fix_docs_continues_when_link_rewriter_and_validator_fail(tmp_path):
    from codewiki.src.be.docs_fixer import fix_docs

    (tmp_path / "overview.md").write_text("# Overview\n", encoding="utf-8")
    config = MagicMock()
    config.postprocess = MagicMock()
    config.postprocess.fix_links = True
    config.postprocess.strict = False

    with (
        patch(
            "codewiki.src.be.docs_fixer.rewrite_broken_links",
            side_effect=RuntimeError("rewrite boom"),
            create=True,
        ),
        patch(
            "codewiki.src.be.docs_fixer.validate_links",
            side_effect=RuntimeError("validate boom"),
            create=True,
        ),
    ):
        stats = fix_docs(str(tmp_path), config)

    assert stats.md_files_formatted >= 0
    assert (tmp_path / "_lint_report.json").exists()


def test_fix_docs_uses_new_math_validator(tmp_path):
    from codewiki.src.be.docs_fixer import fix_docs
    from codewiki.src.codewiki_config import CodeWikiConfig, PostprocessConfig

    (tmp_path / "test.md").write_text("$$\\frac{1}{2}$$", encoding="utf-8")
    config = CodeWikiConfig(
        repo_path=str(tmp_path),
        docs_dir=str(tmp_path),
        postprocess=PostprocessConfig(fix_links=False),
        main_model="openai/gpt-4o-mini",
    )

    with patch("codewiki.src.be.docs_fixer.fix_math", return_value="$$\\frac{1}{2}$$") as mock_math:
        fix_docs(str(tmp_path), config)
    mock_math.assert_called()


def test_fix_docs_uses_new_mermaid_validator(tmp_path):
    from codewiki.src.be.docs_fixer import fix_docs
    from codewiki.src.codewiki_config import CodeWikiConfig, PostprocessConfig

    (tmp_path / "test.md").write_text("```mermaid\ngraph TD\nA-->B\n```", encoding="utf-8")
    config = CodeWikiConfig(
        repo_path=str(tmp_path),
        docs_dir=str(tmp_path),
        postprocess=PostprocessConfig(fix_links=False),
        main_model="openai/gpt-4o-mini",
    )

    with patch(
        "codewiki.src.be.docs_fixer.fix_mermaid",
        return_value="```mermaid\ngraph TD\nA-->B\n```",
    ) as mock_mermaid:
        fix_docs(str(tmp_path), config)
    mock_mermaid.assert_called()
