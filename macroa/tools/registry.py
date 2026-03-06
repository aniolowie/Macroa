"""ToolRegistry — discovers, loads, and converts tools into SkillEntry objects.

Discovery path (both scanned at startup):
  1. macroa/tools/examples/  — built-in reference tools (ship with Macroa)
  2. ~/.macroa/tools/         — user-installed tools (drop a folder here to install)

Tool directory format:
  ~/.macroa/tools/
    my_tool/
      tool.py          ← required: exports MANIFEST and one BaseTool subclass
      .env             ← optional: tool-specific secrets loaded via dotenv
      helpers.py       ← optional: any other files the tool needs

After load_from_dir(), call inject_into(skill_registry) to make all tools
available to the router and dispatcher alongside built-in skills.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from macroa.config.skill_registry import SkillEntry, SkillRegistry
from macroa.stdlib.schema import DriverBundle, SkillManifest
from macroa.tools.base import BaseTool, ToolManifest
from macroa.tools.runner import ToolRunner

logger = logging.getLogger(__name__)


class ToolEntry:
    def __init__(self, manifest: ToolManifest, tool: BaseTool) -> None:
        self.manifest = manifest
        self.tool = tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def load_from_dir(self, tools_dir: Path, drivers: DriverBundle | None = None) -> None:
        """Scan a directory for tool subdirectories and load each one."""
        if not tools_dir.exists():
            return

        for tool_dir in sorted(tools_dir.iterdir()):
            if not tool_dir.is_dir() or tool_dir.name.startswith("_"):
                continue
            tool_file = tool_dir / "tool.py"
            if not tool_file.exists():
                continue
            self._load_tool(tool_dir, tool_file, drivers)

    def _load_tool(
        self, tool_dir: Path, tool_file: Path, drivers: DriverBundle | None
    ) -> None:
        name = tool_dir.name
        module_name = f"macroa_tool.{name}"

        # Load tool-specific .env if present
        env_file = tool_dir / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)

        try:
            if module_name not in sys.modules:
                spec = importlib.util.spec_from_file_location(module_name, tool_file)
                if spec is None or spec.loader is None:
                    logger.warning("Skipping tool %s — cannot create module spec", name)
                    return
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)  # type: ignore[union-attr]
            else:
                module = sys.modules[module_name]

            # Validate MANIFEST
            manifest = getattr(module, "MANIFEST", None)
            if not isinstance(manifest, ToolManifest):
                logger.warning(
                    "Skipping tool %s — MANIFEST missing or not a ToolManifest", name
                )
                return

            # Find the BaseTool subclass
            tool_class = None
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseTool)
                    and obj is not BaseTool
                ):
                    tool_class = obj
                    break

            if tool_class is None:
                logger.warning(
                    "Skipping tool %s — no BaseTool subclass found in tool.py", name
                )
                return

            tool_instance = tool_class()

            # Run setup() once — log failures but don't abort loading
            if drivers is not None:
                try:
                    tool_instance.setup(drivers)
                except Exception as exc:
                    logger.warning("Tool %s setup() failed: %s", name, exc)

            self._tools[manifest.name] = ToolEntry(manifest=manifest, tool=tool_instance)
            logger.debug("Loaded tool: %s v%s by %s", manifest.name, manifest.version, manifest.author)

        except Exception as exc:
            logger.warning("Skipping tool %s — load error: %s", name, exc)

    def inject_into(self, skill_registry: SkillRegistry) -> None:
        """Convert all loaded tools into SkillEntry objects and register them."""
        for entry in self._tools.values():
            runner = ToolRunner(timeout=entry.manifest.timeout)
            run_fn = runner.wrap(entry.tool, entry.manifest)

            # Adapt ToolManifest → SkillManifest so the router can list it
            skill_manifest = SkillManifest(
                name=entry.manifest.name,
                description=f"[tool v{entry.manifest.version}] {entry.manifest.description}",
                triggers=entry.manifest.triggers,
                model_tier=entry.manifest.model_tier,
                deterministic=False,
            )
            skill_registry.register(SkillEntry(skill_manifest, run_fn))

    def persistent_tools(self) -> list[ToolEntry]:
        """Return all tools with persistent=True (candidates for heartbeat)."""
        return [e for e in self._tools.values() if e.manifest.persistent]

    def teardown_all(self, drivers: DriverBundle) -> None:
        """Call teardown() on all tools — invoke on clean shutdown."""
        for entry in self._tools.values():
            try:
                entry.tool.teardown(drivers)
            except Exception as exc:
                logger.warning("Tool %s teardown() failed: %s", entry.manifest.name, exc)
