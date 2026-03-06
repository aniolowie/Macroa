"""Macroa VFS — unified virtual file system."""

from .base import VFSBackend, VFSError, VFSNode
from .vfs import VFS

__all__ = ["VFS", "VFSBackend", "VFSError", "VFSNode"]
