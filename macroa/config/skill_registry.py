"""Auto-discovery and registry for skill modules in macroa/skills/."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Callable

from macroa.stdlib.schema import SkillManifest, Intent, Context, SkillResult, DriverBundle

logger = logging.getLogger(__name__)

SkillRunFn = Callable[[Intent, Context, DriverBundle], SkillResult]


class SkillEntry:
    def __init__(self, manifest: SkillManifest, run_fn: SkillRunFn) -> None:
        self.manifest = manifest
        self.run = run_fn


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillEntry] = {}

    def load_from_dir(self, skills_dir: Path) -> None:
        for path in sorted(skills_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"macroa.skills.{path.stem}"
            try:
                if module_name in sys.modules:
                    module = sys.modules[module_name]
                else:
                    spec = importlib.util.spec_from_file_location(module_name, path)
                    if spec is None or spec.loader is None:
                        logger.warning("Skipping %s — cannot create module spec", path.name)
                        continue
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)  # type: ignore[union-attr]

                manifest = getattr(module, "MANIFEST", None)
                run_fn = getattr(module, "run", None)

                if not isinstance(manifest, SkillManifest):
                    logger.warning(
                        "Skipping %s — MANIFEST is missing or not a SkillManifest", path.name
                    )
                    continue
                if not callable(run_fn):
                    logger.warning(
                        "Skipping %s — run() is missing or not callable", path.name
                    )
                    continue

                self._skills[manifest.name] = SkillEntry(manifest, run_fn)
                logger.debug("Loaded skill: %s", manifest.name)

            except Exception as exc:
                logger.warning("Skipping %s — import error: %s", path.name, exc)

    def register(self, entry: SkillEntry) -> None:
        """Directly register a SkillEntry (used by ToolRegistry to inject tools)."""
        self._skills[entry.manifest.name] = entry

    def get(self, name: str) -> SkillEntry | None:
        return self._skills.get(name)

    def all_manifests(self) -> list[SkillManifest]:
        return [entry.manifest for entry in self._skills.values()]

    def names(self) -> list[str]:
        return list(self._skills.keys())
