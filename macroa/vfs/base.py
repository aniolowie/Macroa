"""VFS abstract base — backend interface and shared types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VFSNode:
    """Stat result for any VFS path — equivalent to os.stat_result but backend-agnostic."""

    path: str
    is_dir: bool
    size: int | None = None      # None for directories
    modified: float | None = None  # Unix timestamp, None if not tracked
    backend: str = ""


class VFSError(Exception):
    """Raised when no backend is mounted for the requested path."""


class VFSBackend(ABC):
    """Abstract backend — implement to add a new mountable resource type."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier shown in VFSNode.backend and mount listings."""

    @abstractmethod
    def read(self, path: str) -> str:
        """Return the content at path as a string. Raise FileNotFoundError if missing."""

    @abstractmethod
    def write(self, path: str, content: str) -> str:
        """Write content to path. Creates parents as needed. Returns the path written."""

    @abstractmethod
    def list(self, path: str) -> list[str]:
        """Return names of children at path. Empty list if path doesn't exist."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return True if path exists in this backend."""

    @abstractmethod
    def delete(self, path: str) -> bool:
        """Delete path. Returns True if deleted, False if not found."""

    @abstractmethod
    def stat(self, path: str) -> VFSNode | None:
        """Return metadata for path, or None if it doesn't exist."""
