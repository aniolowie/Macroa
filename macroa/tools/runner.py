"""ToolRunner — executes a tool with timeout and full error isolation.

Wraps a BaseTool.execute() call so that:
  - It cannot run longer than manifest.timeout seconds
  - Any unhandled exception produces a clean SkillResult(success=False)
  - The rest of the kernel is never affected by a misbehaving tool

The returned SkillRunFn has the exact same signature as a skill's run(),
so it drops into SkillEntry without any dispatcher changes.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import TYPE_CHECKING

from macroa.stdlib.schema import Context, DriverBundle, Intent, SkillResult

if TYPE_CHECKING:
    from macroa.tools.base import BaseTool, ToolManifest

logger = logging.getLogger(__name__)


class ToolRunner:
    def __init__(self, timeout: int) -> None:
        self._timeout = timeout

    def wrap(self, tool: "BaseTool", manifest: "ToolManifest"):
        """Return a SkillRunFn that runs tool.execute() with timeout + error isolation."""

        def run_fn(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(tool.execute, intent, context, drivers)
                try:
                    result = future.result(timeout=self._timeout)
                    # Guarantee turn_id is set even if the tool forgot
                    if not result.turn_id:
                        result.turn_id = intent.turn_id
                    return result
                except FuturesTimeout:
                    logger.warning("Tool %r timed out after %ds", manifest.name, self._timeout)
                    return SkillResult(
                        output="",
                        success=False,
                        error=f"Tool '{manifest.name}' timed out after {self._timeout}s",
                        turn_id=intent.turn_id,
                        model_tier=intent.model_tier,
                    )
                except Exception as exc:
                    logger.warning("Tool %r raised unhandled exception: %s", manifest.name, exc)
                    return SkillResult(
                        output="",
                        success=False,
                        error=f"Tool '{manifest.name}' error: {exc}",
                        turn_id=intent.turn_id,
                        model_tier=intent.model_tier,
                    )

        run_fn.__name__ = f"{manifest.name}.execute"
        return run_fn
