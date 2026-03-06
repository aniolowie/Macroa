"""Tool base class — defines the program format every tool must follow.

A tool is a userspace program. It differs from a skill in three ways:
  1. Location  — lives in ~/.macroa/tools/<name>/ (user-installed)
  2. Lifecycle — setup(), execute(), heartbeat(), teardown()
  3. Manifest  — richer metadata: version, author, persistent flag, timeout

Writing a tool
--------------
Create ~/.macroa/tools/my_tool/tool.py with:

    from macroa.tools.base import BaseTool, ToolManifest
    from macroa.stdlib.schema import Context, DriverBundle, Intent, ModelTier, SkillResult

    MANIFEST = ToolManifest(
        name="my_tool",
        description="What this program does.",
        triggers=["trigger phrase", "another phrase"],
        version="1.0.0",
        author="you",
    )

    class MyTool(BaseTool):
        def execute(self, intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
            # do the work
            return SkillResult(output="done", success=True, turn_id=intent.turn_id)

Rules (same as skills):
  - Never raise from execute() — catch and return SkillResult(success=False, error=...)
  - Never mutate the Context received
  - Read tool-specific config from environment variables or ~/.macroa/tools/<name>/.env
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from macroa.stdlib.schema import Context, DriverBundle, Intent, ModelTier, SkillResult


@dataclass
class ToolManifest:
    name: str
    description: str
    triggers: list[str]
    version: str = "1.0.0"
    author: str = "user"
    model_tier: ModelTier | None = None   # None = NANO default (cheapest)
    persistent: bool = False              # True → heartbeat() is called on interval
    timeout: int = 60                     # seconds before ToolRunner kills the call


class BaseTool(ABC):
    """Abstract base class every tool must extend."""

    @abstractmethod
    def execute(self, intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
        """Run the tool. Never raise — always return SkillResult."""
        raise NotImplementedError

    def setup(self, drivers: DriverBundle) -> None:
        """Called once when the tool is first registered.
        Use for one-time initialisation: creating DB tables, checking API keys, etc.
        Failures here are logged but do not prevent registration.
        """

    def heartbeat(self, drivers: DriverBundle) -> None:
        """Called periodically by HeartbeatManager when persistent=True.
        Examples: polling an inbox, syncing a calendar, checking sensor readings.
        Must not block for longer than a few seconds.
        """

    def teardown(self, drivers: DriverBundle) -> None:
        """Called on clean shutdown. Release external connections, flush state."""
