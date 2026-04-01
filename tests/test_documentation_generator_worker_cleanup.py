"""
Tests that _run_module_queue releases worker closure references to the tqdm
progress bar before calling progress.close().

Regression for the tqdm GC / 'lost sys.stderr' bug:
  - Workers hold a closure reference to `progress`.
  - If cancel() is called but workers are not awaited before close(), the
    tqdm object's refcount stays > 1 and GC is deferred.
  - The GC then fires at the start of the next _run_module_queue call (fill
    pass) when sys.stderr may already be in a bad state, producing
    ValueError('I/O operation on closed file.') + AttributeError on __del__.
"""

import asyncio
import gc
import weakref
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_minimal_tree():
    return {"mod_a": {"components": [], "children": {}}}


def _make_generator(tmp_path):
    from codewiki.src.be.documentation_generator import DocumentationGenerator
    from codewiki.src.config import Config

    config = Config(
        repo_path=str(tmp_path),
        output_dir=str(tmp_path / "output"),
        dependency_graph_dir=str(tmp_path / "graphs"),
        docs_dir=str(tmp_path / "docs"),
        max_depth=2,
        llm_base_url="http://localhost/",
        llm_api_key="test-key",
        main_model="test/model",
        cluster_model="test/model",
        max_concurrent=2,
    )

    gen = DocumentationGenerator.__new__(DocumentationGenerator)
    gen.config = config

    async def _fake_process(*args, **kwargs):
        return None, "test/model"

    async def _fake_overview(*args, **kwargs):
        return None

    orchestrator = MagicMock()
    orchestrator.process_module.side_effect = _fake_process
    gen.agent_orchestrator = orchestrator
    gen._module_doc_exists = MagicMock(return_value=True)
    gen.generate_parent_module_docs = _fake_overview
    return gen


def _make_tree_manager(tree):
    tm = MagicMock()

    async def _snap():
        return tree

    tm.get_snapshot.side_effect = _snap
    return tm


# ── tests ─────────────────────────────────────────────────────────────────────

def test_progress_bar_closed_after_queue_completes(tmp_path):
    """progress.close() must be called after the queue drains."""
    gen = _make_generator(tmp_path)
    tree = _make_minimal_tree()
    tm = _make_tree_manager(tree)
    closed_calls = []

    class TrackingTqdm:
        def __init__(self, *a, **kw):
            pass

        def set_postfix_str(self, *a, **kw):
            pass

        def update(self, n=1):
            pass

        def close(self):
            closed_calls.append(True)

    with patch("codewiki.src.be.documentation_generator.tqdm", TrackingTqdm):
        asyncio.run(gen._run_module_queue(
            tree, {}, str(tmp_path), tm,
            desc="test", include_root=False,
        ))

    assert closed_calls, "progress.close() was never called"


def test_worker_closures_release_progress_before_close(tmp_path):
    """
    After _run_module_queue returns, the tqdm object must not be kept alive
    by worker task closures.

    Regression: cancel() without await left workers alive, keeping refcount
    > 1 and deferring GC to the next _run_module_queue call (fill pass).
    """
    gen = _make_generator(tmp_path)
    tree = _make_minimal_tree()
    tm = _make_tree_manager(tree)
    progress_ref: list = []

    class CaptureTqdm:
        def __init__(self, *a, **kw):
            self._closed = False
            progress_ref.append(weakref.ref(self))

        def set_postfix_str(self, *a, **kw):
            pass

        def update(self, n=1):
            pass

        def close(self):
            self._closed = True

    with patch("codewiki.src.be.documentation_generator.tqdm", CaptureTqdm):
        asyncio.run(gen._run_module_queue(
            tree, {}, str(tmp_path), tm,
            desc="test", include_root=False,
        ))

    assert progress_ref, "tqdm was never instantiated"

    gc.collect()
    obj = progress_ref[0]()

    # The weakref should be dead (refcount → 0) OR the object must be closed.
    # If workers still hold closure refs, the weakref stays alive after GC —
    # that is the bug that causes 'lost sys.stderr' on the fill pass.
    assert obj is None or obj._closed, (
        "tqdm object still referenced by worker closures after "
        "_run_module_queue returned; this causes deferred GC and the "
        "'lost sys.stderr' / AttributeError on fill pass."
    )


def test_progress_closed_on_cancellation(tmp_path):
    """
    If the outer task is cancelled mid-run, the finally block must still
    call progress.close() so no tqdm object leaks to the next call.
    """
    gen = _make_generator(tmp_path)
    tree = _make_minimal_tree()
    tm = _make_tree_manager(tree)
    close_called = []

    class CountingTqdm:
        def __init__(self, *a, **kw):
            pass

        def set_postfix_str(self, *a, **kw):
            pass

        def update(self, n=1):
            pass

        def close(self):
            close_called.append(True)

    async def _cancel_join(self):
        raise asyncio.CancelledError()

    with patch("codewiki.src.be.documentation_generator.tqdm", CountingTqdm):
        with patch("asyncio.Queue.join", _cancel_join):
            with pytest.raises((asyncio.CancelledError, Exception)):
                asyncio.run(gen._run_module_queue(
                    tree, {}, str(tmp_path), tm,
                    desc="test", include_root=False,
                ))

    assert close_called, "progress.close() not called after CancelledError"
