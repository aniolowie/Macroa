"""LocalBackend — mounts a real filesystem directory into the VFS."""

from __future__ import annotations

from pathlib import Path

from .base import VFSBackend, VFSNode


class LocalBackend(VFSBackend):
    """Maps VFS paths to a real directory tree rooted at `base`.

    Example:
        backend = LocalBackend(Path.home() / ".macroa" / "identity", label="identity")
        # VFS path "/" within this backend → ~/.macroa/identity/
        # VFS path "/IDENTITY.md"          → ~/.macroa/identity/IDENTITY.md
    """

    def __init__(self, base: Path, label: str = "local") -> None:
        self._base = Path(base).expanduser().resolve()
        self._label = label

    @property
    def name(self) -> str:
        return self._label

    def _resolve(self, path: str) -> Path:
        rel = path.lstrip("/")
        return self._base / rel if rel else self._base

    def read(self, path: str) -> str:
        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError(f"Not found: {path!r}")
        if p.is_dir():
            raise IsADirectoryError(f"Is a directory: {path!r}")
        return p.read_text(encoding="utf-8")

    def write(self, path: str, content: str) -> str:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return path

    def list(self, path: str) -> list[str]:
        p = self._resolve(path)
        if not p.exists():
            return []
        if p.is_file():
            return [p.name]
        return sorted(child.name for child in p.iterdir())

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def delete(self, path: str) -> bool:
        p = self._resolve(path)
        if not p.exists():
            return False
        if p.is_file():
            p.unlink()
            return True
        return False

    def stat(self, path: str) -> VFSNode | None:
        p = self._resolve(path)
        if not p.exists():
            return None
        s = p.stat()
        return VFSNode(
            path=path,
            is_dir=p.is_dir(),
            size=s.st_size if p.is_file() else None,
            modified=s.st_mtime,
            backend=self._label,
        )
