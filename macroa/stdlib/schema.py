"""Core shared dataclasses — imported by every layer of the stack."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from macroa.drivers.fs_driver import FSDriver
    from macroa.drivers.llm_driver import LLMDriver
    from macroa.drivers.memory_driver import MemoryDriver
    from macroa.drivers.network_driver import NetworkDriver
    from macroa.drivers.shell_driver import ShellDriver


class ModelTier(StrEnum):
    # Hardware analogy:
    # NANO   — microcontroller/background thread (routing, trivial ops)
    # HAIKU  — efficiency cores / E-cores       (lightweight tasks)
    # SONNET — performance cores / P-cores      (quality work)
    # OPUS   — GPU                              (heavy reasoning, use sparingly)
    NANO = "nano"
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


@dataclass(frozen=True)
class Intent:
    raw: str
    skill_name: str
    parameters: dict[str, Any]
    model_tier: ModelTier
    routing_confidence: float
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SkillResult:
    output: str
    success: bool
    needs_reasoning: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    pin_to_context: bool = False
    turn_id: str = ""
    model_tier: ModelTier = ModelTier.NANO


@dataclass
class ContextEntry:
    turn_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    pinned: bool = False
    timestamp: float = field(default_factory=time.time)
    skill_name: str | None = None


@dataclass
class Context:
    entries: list[ContextEntry]
    session_id: str


@dataclass
class SkillManifest:
    name: str
    description: str
    triggers: list[str]
    model_tier: ModelTier | None  # None = kernel default (NANO)
    deterministic: bool = True


@dataclass
class DriverBundle:
    llm: LLMDriver
    shell: ShellDriver
    fs: FSDriver
    memory: MemoryDriver
    network: NetworkDriver
    vfs: Any = None     # VFS instance — None in unit tests, always set in production
    budget: Any = None  # BudgetManager — None in unit tests, always set in production
    ipc: Any = None     # IPCBus — None in unit tests, always set in production
    # Per-request streaming callback — set by the REPL, None for single-shot runs.
    # chat_skill checks this and yields tokens via llm.stream() when set.
    stream_callback: Callable[[str], None] | None = None
