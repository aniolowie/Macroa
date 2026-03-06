"""VFS — unified path-addressable layer over all mounted backends.

Mount table (longest prefix wins):
    /mem/       → MemoryBackend   (SQLite facts as key/value files)
    /identity/  → LocalBackend   (~/.macroa/identity/)
    /workspace/ → LocalBackend   (~/.macroa/workspace/)
    /research/  → LocalBackend   (~/.macroa/research/)
    /tools/     → LocalBackend   (~/.macroa/tools/)
    /logs/      → LocalBackend   (~/.macroa/logs/)
    /sessions/  → LocalBackend   (~/.macroa/sessions/)
    /fs/        → LocalBackend   (/ — full filesystem, elevated access)

Any path not matching a prefix raises VFSError.
"""

from __future__ import annotations

import logging

from .base import VFSBackend, VFSError, VFSNode

logger = logging.getLogger(__name__)


class VFS:
    """Virtual File System — routes paths to the correct backend."""

    def __init__(self) -> None:
        # Sorted longest-prefix first so most-specific mount always wins
        self._mounts: list[tuple[str, VFSBackend]] = []

    def mount(self, prefix: str, backend: VFSBackend) -> None:
        """Register a backend at a path prefix (e.g. '/mem', '/identity')."""
        prefix = "/" + prefix.strip("/")  # normalise — always starts with /
        self._mounts.append((prefix, backend))
        self._mounts.sort(key=lambda x: len(x[0]), reverse=True)
        logger.debug("VFS: mounted %r → %s", prefix, backend.name)

    def _resolve(self, path: str) -> tuple[VFSBackend, str]:
        """Return (backend, path_within_backend) for a VFS path."""
        if not path.startswith("/"):
            path = "/" + path
        for prefix, backend in self._mounts:
            if prefix == "/":
                return backend, path
            if path == prefix or path.startswith(prefix + "/"):
                remainder = path[len(prefix):]
                return backend, remainder or "/"
        raise VFSError(f"No backend mounted for path: {path!r}")

    # ── Public interface ──────────────────────────────────────────────────────

    def read(self, path: str) -> str:
        backend, rel = self._resolve(path)
        return backend.read(rel)

    def write(self, path: str, content: str) -> str:
        backend, rel = self._resolve(path)
        return backend.write(rel, content)

    def list(self, path: str) -> list[str]:
        backend, rel = self._resolve(path)
        return backend.list(rel)

    def exists(self, path: str) -> bool:
        try:
            backend, rel = self._resolve(path)
            return backend.exists(rel)
        except VFSError:
            return False

    def delete(self, path: str) -> bool:
        backend, rel = self._resolve(path)
        return backend.delete(rel)

    def stat(self, path: str) -> VFSNode | None:
        try:
            backend, rel = self._resolve(path)
            return backend.stat(rel)
        except VFSError:
            return None

    def mounts(self) -> list[tuple[str, str]]:
        """Return [(prefix, backend_name)] — useful for diagnostics."""
        return [(prefix, b.name) for prefix, b in self._mounts]
