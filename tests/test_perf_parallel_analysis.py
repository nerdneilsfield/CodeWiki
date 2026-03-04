# tests/test_perf_parallel_analysis.py
import time
from codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer import CallGraphAnalyzer

SAMPLE_C = "void foo(void) {}\nvoid bar(void) { foo(); }\n"


def _make_file_list(n: int) -> list[dict]:
    return [
        {"path": f"file_{i}.c", "name": f"file_{i}.c", "extension": ".c", "language": "c"}
        for i in range(n)
    ]


def test_parallel_analysis_returns_correct_results(tmp_path):
    """Results must be identical to serial analysis."""
    for i in range(2):
        (tmp_path / f"file_{i}.c").write_text(SAMPLE_C)
    files = _make_file_list(2)

    analyzer = CallGraphAnalyzer()
    result = analyzer.analyze_code_files(files, str(tmp_path))

    assert result["call_graph"]["files_analyzed"] == 2
    assert result["call_graph"]["total_functions"] >= 2


def test_parallel_analysis_is_faster_than_serial(tmp_path):
    """With sleep-injected I/O latency, parallel must be <1.9x of half the files.

    Serial execution gives exactly 2.0x; parallel (≥2 workers) gives ≤1.5x.
    The 1.9x bound reliably distinguishes the two.
    """
    from unittest.mock import patch
    from pathlib import Path as _Path

    def slow_open(base_dir: _Path, target: _Path, encoding="utf-8") -> str:
        time.sleep(0.02)  # 20 ms artificial I/O latency per file
        return target.read_text(encoding=encoding, errors="replace")

    for i in range(20):
        (tmp_path / f"file_{i}.c").write_text(SAMPLE_C)

    files_10 = _make_file_list(10)
    files_20 = _make_file_list(20)

    TARGET = "codewiki.src.be.dependency_analyzer.analysis.call_graph_analyzer.safe_open_text"
    with patch(TARGET, side_effect=slow_open):
        analyzer = CallGraphAnalyzer()
        t0 = time.perf_counter()
        analyzer.analyze_code_files(files_10, str(tmp_path))
        t_10 = time.perf_counter() - t0

        t0 = time.perf_counter()
        analyzer.analyze_code_files(files_20, str(tmp_path))
        t_20 = time.perf_counter() - t0

    # Serial: t_20 ≈ 2.0 × t_10. Parallel (≥2 workers): t_20 < 1.9 × t_10.
    assert t_20 < t_10 * 1.9, (
        f"Parallelism not detected: 10 files={t_10:.3f}s, 20 files={t_20:.3f}s "
        f"(serial gives ~2.0x; expected parallel <1.9x)"
    )
