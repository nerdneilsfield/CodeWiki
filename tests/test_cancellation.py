import threading

import pytest

from codewiki.src.be.errors import CancellationError


class TestCancellationToken:
    def test_not_cancelled_initially(self):
        from codewiki.src.be.cancellation import CancellationToken

        token = CancellationToken()
        assert not token.is_cancelled

    def test_cancel_sets_flag(self):
        from codewiki.src.be.cancellation import CancellationToken

        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled

    def test_check_raises_when_cancelled(self):
        from codewiki.src.be.cancellation import CancellationToken

        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancellationError):
            token.check()

    def test_check_does_not_raise_when_not_cancelled(self):
        from codewiki.src.be.cancellation import CancellationToken

        token = CancellationToken()
        token.check()

    def test_thread_safe(self):
        from codewiki.src.be.cancellation import CancellationToken

        token = CancellationToken()
        results = []

        def cancel_from_thread():
            token.cancel()
            results.append("cancelled")

        t = threading.Thread(target=cancel_from_thread)
        t.start()
        t.join()
        assert token.is_cancelled
        assert results == ["cancelled"]


class TestPipelineRunnerWithCancelToken:
    @pytest.mark.asyncio
    async def test_cancels_before_stage(self):
        from codewiki.src.be.cancellation import CancellationToken
        from codewiki.src.be.pipeline import PipelineContext, PipelineRunner

        executed = []

        class Stage:
            name = "stage1"
            failure_policy = "degraded_ok"

            async def execute(self, ctx):
                executed.append(self.name)

        token = CancellationToken()
        token.cancel()
        ctx = PipelineContext(config=None, cancel_token=token)

        runner = PipelineRunner([Stage()])
        result = await runner.execute(ctx)

        assert result.status == "cancelled"
        assert "stage1" not in executed

    @pytest.mark.asyncio
    async def test_stage_internal_cancellation_sets_cancelled(self):
        from codewiki.src.be.pipeline import PipelineContext, PipelineRunner

        executed_after = []

        class CancellingStage:
            name = "cancelling"
            failure_policy = "degraded_ok"

            async def execute(self, ctx):
                raise CancellationError("cancelled mid-stage")

        class NextStage:
            name = "next"
            failure_policy = "degraded_ok"

            async def execute(self, ctx):
                executed_after.append(self.name)

        ctx = PipelineContext(config=None)
        runner = PipelineRunner([CancellingStage(), NextStage()])
        result = await runner.execute(ctx)

        assert result.status == "cancelled"
        assert "next" not in executed_after
