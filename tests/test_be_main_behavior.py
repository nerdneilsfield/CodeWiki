from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_main_logs_warning_for_degraded_result():
    from codewiki.src.be import main as mod
    from codewiki.src.be.pipeline import GenerationResult

    generator = MagicMock()
    generator.run = AsyncMock(return_value=GenerationResult(status="degraded", warnings=["index"]))

    with (
        patch.object(mod, "parse_arguments", return_value=SimpleNamespace()),
        patch.object(mod, "_build_runtime_config_from_args", return_value=MagicMock()),
        patch.object(mod, "DocumentationGenerator", return_value=generator),
        patch.object(mod.logger, "warning") as mock_warning,
    ):
        await mod.main()

    mock_warning.assert_called_once()


@pytest.mark.asyncio
async def test_main_raises_runtime_error_for_failed_result():
    from codewiki.src.be import main as mod
    from codewiki.src.be.pipeline import GenerationResult

    generator = MagicMock()
    generator.run = AsyncMock(return_value=GenerationResult(status="failed", warnings=["boom"]))

    with (
        patch.object(mod, "parse_arguments", return_value=SimpleNamespace()),
        patch.object(mod, "_build_runtime_config_from_args", return_value=MagicMock()),
        patch.object(mod, "DocumentationGenerator", return_value=generator),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await mod.main()


@pytest.mark.asyncio
async def test_main_swallows_keyboard_interrupt():
    from codewiki.src.be import main as mod

    with (
        patch.object(mod, "parse_arguments", side_effect=KeyboardInterrupt),
        patch.object(mod.logger, "debug") as mock_debug,
    ):
        await mod.main()

    mock_debug.assert_called_once()
