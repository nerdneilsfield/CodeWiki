from unittest.mock import MagicMock, patch

import pytest


def test_progress_tracker_overall_progress_uses_stage_weights():
    from codewiki.cli.utils.progress import ProgressTracker

    tracker = ProgressTracker(verbose=False)
    tracker.current_stage = 3
    tracker.stage_progress = 0.5

    assert tracker.get_overall_progress() == pytest.approx(0.75)


def test_progress_tracker_get_eta_formats_hours(monkeypatch):
    from codewiki.cli.utils import progress
    from codewiki.cli.utils.progress import ProgressTracker

    tracker = ProgressTracker(verbose=False)
    tracker.start_time = 0
    tracker.current_stage = 1
    tracker.stage_progress = 0.1

    monkeypatch.setattr(progress.time, "time", lambda: 3600)

    assert tracker.get_eta() == "23h 59m"


def test_progress_tracker_complete_stage_logs_message_in_verbose():
    from codewiki.cli.utils.progress import ProgressTracker

    tracker = ProgressTracker(verbose=True)
    tracker.current_stage = 2
    tracker.current_stage_start = 0
    tracker.start_time = 0

    with (
        patch("codewiki.cli.utils.progress.time.time", return_value=5),
        patch("codewiki.cli.utils.progress.logger.info") as info,
    ):
        tracker.complete_stage("done")

    assert tracker.stage_progress == 1.0
    assert info.call_count == 2


def test_module_progress_bar_updates_click_bar_when_not_verbose():
    from codewiki.cli.utils.progress import ModuleProgressBar

    fake_ctx = MagicMock()
    fake_ctx.__enter__.return_value = fake_ctx
    fake_ctx.__exit__.return_value = None

    with patch("codewiki.cli.utils.progress.click.progressbar", return_value=fake_ctx):
        progress = ModuleProgressBar(total_modules=2, verbose=False)
        progress.update("mod")
        progress.finish()

    fake_ctx.__enter__.assert_called_once()
    fake_ctx.update.assert_called_once_with(1)
    fake_ctx.__exit__.assert_called_once()


def test_module_progress_bar_logs_in_verbose_mode():
    from codewiki.cli.utils.progress import ModuleProgressBar

    with patch("codewiki.cli.utils.progress.logger.info") as info:
        progress = ModuleProgressBar(total_modules=3, verbose=True)
        progress.update("module-a", cached=True)

    assert progress.current_module == 1
    info.assert_called_once()
