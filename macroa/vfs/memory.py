"""MemoryBackend — exposes the SQLite memory driver as a VFS namespace.

Path scheme (relative to the /mem mount point):
    /                    → list all namespaces
    /<namespace>/        → list all keys in a namespace
    /<namespace>/<key>   → read/write/delete a fact value
"""

from __future__ import annotations

from .base import VFSBackend, VFSNode


class MemoryBackend(VFSBackend):
    """Wraps MemoryDriver so memory facts are addressable as VFS paths.

    Example VFS paths (after mounting at /mem):
        /mem/user/name          → drivers.memory.get("user", "name")
        /mem/user/              → list all keys in "user" namespace
        /mem/                   → list all namespaces
    """

    def __init__(self, memory_driver) -> None:
        self._mem = memory_driver

    @property
    def name(self) -> str:
        return "memory"

    def _parse(self, path: str) -> tuple[str | None, str | None]:
        """Return (namespace, key) from a relative path like 'user/name'."""
        parts = [p for p in path.strip("/").split("/") if p]
        ns = parts[0] if parts else None
        key = parts[1] if len(parts) > 1 else None
        return ns, key

    def read(self, path: str) -> str:
        ns, key = self._parse(path)
        if not ns or not key:
            raise ValueError(f"Memory read requires /<namespace>/<key>, got: {path!r}")
        value = self._mem.get(ns, key)
        if value is None:
            raise FileNotFoundError(f"No memory fact at {path!r}")
        return value

    def write(self, path: str, content: str) -> str:
        ns, key = self._parse(path)
        if not ns or not key:
            raise ValueError(f"Memory write requires /<namespace>/<key>, got: {path!r}")
        self._mem.set(ns, key, content)
        return path

    def list(self, path: str) -> list[str]:
        ns, key = self._parse(path)
        if not ns:
            # Root: list all namespaces
            all_facts = self._mem.list_all(namespace=None)
            return sorted({f["namespace"] for f in all_facts})
        # Namespace dir: list all keys
        facts = self._mem.list_all(namespace=ns)
        return [f["key"] for f in facts]

    def exists(self, path: str) -> bool:
        ns, key = self._parse(path)
        if not ns:
            return True  # root always exists
        if not key:
            return len(self._mem.list_all(namespace=ns)) > 0
        return self._mem.get(ns, key) is not None

    def delete(self, path: str) -> bool:
        ns, key = self._parse(path)
        if not ns or not key:
            return False
        return self._mem.delete(ns, key)

    def stat(self, path: str) -> VFSNode | None:
        ns, key = self._parse(path)
        if not ns:
            return VFSNode(path=path, is_dir=True, backend="memory")
        if not key:
            return VFSNode(path=path, is_dir=True, backend="memory")
        value = self._mem.get(ns, key)
        if value is None:
            return None
        return VFSNode(path=path, is_dir=False, size=len(value), backend="memory")
