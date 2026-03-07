"""macroa.memory — contextual memory pipeline (extract → retrieve → format)."""

from macroa.memory.extractor import MemoryExtractor
from macroa.memory.formatter import format_for_prompt
from macroa.memory.retriever import retrieve

__all__ = ["MemoryExtractor", "format_for_prompt", "retrieve"]
