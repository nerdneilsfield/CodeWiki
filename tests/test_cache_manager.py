import os
import sys
import threading
import time

import pytest

from codewiki.src.be.cache_manager import CacheEntry, CacheManager


@pytest.fixture
def cache_dir(tmp_path):
    cache_path = tmp_path / ".codewiki"
    cache_path.mkdir()
    return str(cache_path)


def test_cache_entry_creation():
    entry = CacheEntry(
        artifact_id="module:auth",
        input_hash="abc123",
        status="valid",
        depends_on=[],
        output_path="auth.md",
        output_file="auth.md",
    )
    assert entry.artifact_id == "module:auth"
    assert entry.status == "valid"
    assert entry.attempt_count == 0


def test_cache_manager_is_valid_miss(cache_dir):
    cache_manager = CacheManager(cache_dir)
    assert cache_manager.is_valid("module:auth", "abc123") is False


def test_cache_manager_mark_done_then_valid(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done("module:auth", input_hash="abc123", output_path="auth.md")
    assert cache_manager.is_valid("module:auth", "abc123") is True


def test_cache_manager_stale_on_hash_change(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done("module:auth", input_hash="abc123", output_path="auth.md")
    assert cache_manager.is_valid("module:auth", "different") is False


def test_cache_manager_invalidate_cascades(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done("module:auth", input_hash="h1", output_path="auth.md")
    cache_manager.mark_done(
        "overview:root:child:auth",
        input_hash="h2",
        output_path="p.md",
        depends_on=["module:auth"],
    )
    cache_manager.invalidate("module:auth")
    assert cache_manager.get_entry("module:auth").status == "stale"
    assert cache_manager.get_entry("overview:root:child:auth").status == "stale"


def test_cache_manager_get_output_file(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.plan_task("module:auth", output_file="auth.md")
    assert cache_manager.get_output_file("module:auth") == "auth.md"


def test_cache_manager_plan_task_sets_missing(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.plan_task("module:auth", output_file="auth.md")
    entry = cache_manager.get_entry("module:auth")
    assert entry.status == "missing"
    assert entry.output_file == "auth.md"


def test_cache_manager_plan_task_collision_raises(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.plan_task("module:auth", output_file="auth.md")
    with pytest.raises(ValueError, match="Output file collision"):
        cache_manager.plan_task("module:auth2", output_file="auth.md")


def test_cache_manager_plan_task_ignores_empty_output_file_for_collisions(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.plan_task("module:auth", output_file="")
    cache_manager.plan_task("module:auth2", output_file="")

    assert cache_manager.get_entry("module:auth").output_file == ""
    assert cache_manager.get_entry("module:auth2").output_file == ""


def test_cache_manager_mark_running(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.plan_task("module:auth", output_file="auth.md")
    cache_manager.mark_running("module:auth")
    assert cache_manager.get_entry("module:auth").status == "running"


def test_cache_manager_mark_running_creates_missing_entry(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_running("overview:root")

    entry = cache_manager.get_entry("overview:root")
    assert entry is not None
    assert entry.status == "running"


def test_cache_manager_mark_failed(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.plan_task("module:auth", output_file="auth.md")
    cache_manager.mark_running("module:auth")
    cache_manager.mark_failed("module:auth", error="timeout")
    entry = cache_manager.get_entry("module:auth")
    assert entry.status == "failed"
    assert entry.error == "timeout"


def test_cache_manager_mark_failed_creates_missing_entry(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_failed("module:auth", error="timeout")

    entry = cache_manager.get_entry("module:auth")
    assert entry is not None
    assert entry.status == "failed"
    assert entry.error == "timeout"


def test_cache_manager_flush_and_load(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done(
        "module:auth",
        input_hash="abc",
        output_path="auth.md",
        output_file="auth.md",
    )
    cache_manager.flush()

    registry_path = os.path.join(cache_dir, "cache_registry.json")
    assert os.path.exists(registry_path)

    cache_manager_reloaded = CacheManager(cache_dir)
    assert cache_manager_reloaded.is_valid("module:auth", "abc") is True


def test_cache_manager_get_entry_returns_copy(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done("module:auth", input_hash="abc", output_path="auth.md")

    entry = cache_manager.get_entry("module:auth")
    assert entry is not None
    entry.status = "stale"

    fresh = cache_manager.get_entry("module:auth")
    assert fresh is not None
    assert fresh.status == "valid"


def test_cache_manager_plan_task_updates_valid_entry_persistently(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done(
        "overview:root",
        input_hash="h1",
        output_path="overview.md",
        output_file="overview.md",
        depends_on=["a"],
    )

    cache_manager.plan_task("overview:root", output_file="overview-v2.md", depends_on=["b"])
    cache_manager.flush()

    reloaded = CacheManager(cache_dir)
    entry = reloaded.get_entry("overview:root")
    assert entry is not None
    assert entry.output_file == "overview-v2.md"
    assert entry.depends_on == ["b"]


def test_cache_manager_crash_recovery_running_to_stale(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.plan_task("module:auth", output_file="auth.md")
    cache_manager.mark_running("module:auth")
    cache_manager.flush()

    cache_manager_reloaded = CacheManager(cache_dir)
    entry = cache_manager_reloaded.get_entry("module:auth")
    assert entry.status == "stale"


def test_cache_manager_invalidate_downstream(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done("module:a", input_hash="h1", output_path="a.md")
    cache_manager.mark_done("module:b", input_hash="h2", output_path="b.md")
    cache_manager.mark_done(
        "overview:root:child:a",
        input_hash="h3",
        output_path="ca.md",
        depends_on=["module:a"],
    )
    cache_manager.mark_done(
        "overview:root:child:b",
        input_hash="h4",
        output_path="cb.md",
        depends_on=["module:b"],
    )
    cache_manager.mark_done(
        "overview:root",
        input_hash="h5",
        output_path="overview.md",
        depends_on=["overview:root:child:a", "overview:root:child:b"],
    )
    cache_manager.invalidate("module:a")
    assert cache_manager.get_entry("module:a").status == "stale"
    assert cache_manager.get_entry("overview:root:child:a").status == "stale"
    assert cache_manager.get_entry("overview:root").status == "stale"
    assert cache_manager.get_entry("module:b").status == "valid"
    assert cache_manager.get_entry("overview:root:child:b").status == "valid"


def test_cache_manager_invalidate_handles_deep_dependency_chain(cache_dir):
    cache_manager = CacheManager(cache_dir)
    depth = max(1200, sys.getrecursionlimit() + 50)
    previous = "module:root"
    cache_manager.mark_done(previous, input_hash="h0", output_path="root.md")
    for idx in range(depth):
        artifact_id = f"overview:node:{idx}"
        cache_manager.mark_done(
            artifact_id,
            input_hash=f"h{idx + 1}",
            output_path=f"{idx}.md",
            depends_on=[previous],
        )
        previous = artifact_id

    cache_manager.invalidate("module:root")

    assert cache_manager.get_entry("module:root").status == "stale"
    assert cache_manager.get_entry(previous).status == "stale"


def test_cache_manager_invalidate_handles_cycle(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done(
        "module:a",
        input_hash="ha",
        output_path="a.md",
        depends_on=["module:b"],
    )
    cache_manager.mark_done(
        "module:b",
        input_hash="hb",
        output_path="b.md",
        depends_on=["module:a"],
    )

    cache_manager.invalidate("module:a")

    assert cache_manager.get_entry("module:a").status == "stale"
    assert cache_manager.get_entry("module:b").status == "stale"


def test_cache_manager_get_stale_entries(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done("module:a", input_hash="h1", output_path="a.md")
    cache_manager.plan_task("module:b", output_file="b.md")
    stale = cache_manager.get_stale_entries(prefix="module:")
    assert len(stale) == 1
    assert stale[0].artifact_id == "module:b"


def test_overview_regenerate_threshold():
    assert CacheManager.OVERVIEW_REGENERATE_THRESHOLD == 0.5


def test_cache_manager_stop_wakes_flush_thread_immediately(cache_dir):
    cache_manager = CacheManager(cache_dir, flush_interval=60.0)
    cache_manager.start()
    started = time.monotonic()
    cache_manager.stop()
    assert time.monotonic() - started < 1.0


def test_cache_manager_flush_removes_tmp_on_failure(cache_dir, monkeypatch):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done("module:auth", input_hash="abc", output_path="auth.md")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("json.dump", _boom)
    with pytest.raises(OSError, match="disk full"):
        cache_manager.flush()

    assert not os.path.exists(os.path.join(cache_dir, "cache_registry.json.tmp"))


def test_cache_manager_concurrent_get_entry_mutation_is_isolated(cache_dir):
    cache_manager = CacheManager(cache_dir)
    cache_manager.mark_done("module:auth", input_hash="abc", output_path="auth.md")
    stop = threading.Event()
    errors: list[Exception] = []

    def _reader():
        try:
            while not stop.is_set():
                entry = cache_manager.get_entry("module:auth")
                if entry is not None:
                    entry.status = "stale"
                    entry.output_file = "mutated.md"
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    thread = threading.Thread(target=_reader)
    thread.start()
    try:
        for idx in range(50):
            cache_manager.mark_done(
                "module:auth",
                input_hash=f"abc-{idx}",
                output_path="auth.md",
                output_file="auth.md",
            )
    finally:
        stop.set()
        thread.join(timeout=2.0)

    assert errors == []
    entry = cache_manager.get_entry("module:auth")
    assert entry is not None
    assert entry.output_file == "auth.md"
    assert entry.status == "valid"


def test_cache_full_pipeline_skip_flow(cache_dir):
    cache_manager = CacheManager(cache_dir)

    cache_manager.mark_done(
        "module:auth",
        input_hash="h1",
        output_path="auth.md",
        output_file="auth.md",
    )
    cache_manager.mark_done(
        "module:db",
        input_hash="h2",
        output_path="db.md",
        output_file="db.md",
    )
    cache_manager.mark_done(
        "overview:root:arch_intro",
        input_hash="h3",
        output_path="parts/arch.md",
        output_file="arch.md",
        depends_on=["module:auth", "module:db"],
    )
    cache_manager.mark_done(
        "overview:root:child:module:auth",
        input_hash="h1",
        output_path="parts/auth.md",
        output_file="auth-part.md",
        depends_on=["module:auth"],
    )
    cache_manager.mark_done(
        "overview:root:child:module:db",
        input_hash="h2",
        output_path="parts/db.md",
        output_file="db-part.md",
        depends_on=["module:db"],
    )
    cache_manager.mark_done(
        "overview:root",
        input_hash="h5",
        output_path="overview.md",
        output_file="overview.md",
        depends_on=[
            "overview:root:arch_intro",
            "overview:root:child:module:auth",
            "overview:root:child:module:db",
        ],
    )
    cache_manager.mark_done(
        "guide:getting_started",
        input_hash="h6",
        output_path="guide.md",
        output_file="guide.md",
    )
    cache_manager.flush()

    cache_manager_reloaded = CacheManager(cache_dir)
    assert cache_manager_reloaded.is_valid("module:auth", "h1") is True
    assert cache_manager_reloaded.is_valid("module:db", "h2") is True
    assert cache_manager_reloaded.is_valid("overview:root", "h5") is True
    assert cache_manager_reloaded.is_valid("guide:getting_started", "h6") is True

    assert cache_manager_reloaded.is_valid("module:auth", "h1_changed") is False
    cache_manager_reloaded.invalidate("module:auth")

    assert cache_manager_reloaded.get_entry("overview:root:child:module:auth").status == "stale"
    assert cache_manager_reloaded.get_entry("overview:root:arch_intro").status == "stale"
    assert cache_manager_reloaded.get_entry("overview:root").status == "stale"
    assert cache_manager_reloaded.get_entry("module:db").status == "valid"
    assert cache_manager_reloaded.get_entry("overview:root:child:module:db").status == "valid"
