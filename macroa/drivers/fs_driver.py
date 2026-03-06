"""Filesystem driver — safe reads/writes under home directory."""

from __future__ import annotations

from pathlib import Path


class FSDriverError(Exception):
    pass


class FSDriver:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = (base_dir or Path.home()).resolve()

    def _safe_path(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self._base)
        except ValueError:
            raise FSDriverError(
                f"Path '{resolved}' is outside the allowed base '{self._base}'"
            )
        return resolved

    def read(self, path: str | Path) -> str:
        p = self._safe_path(path)
        if not p.exists():
            raise FSDriverError(f"File not found: {p}")
        if not p.is_file():
            raise FSDriverError(f"Not a file: {p}")
        return p.read_text(encoding="utf-8", errors="replace")

    def write(self, path: str | Path, content: str) -> Path:
        p = self._safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def list_dir(self, path: str | Path) -> list[str]:
        p = self._safe_path(path)
        if not p.is_dir():
            raise FSDriverError(f"Not a directory: {p}")
        return sorted(str(child.name) for child in p.iterdir())

    def exists(self, path: str | Path) -> bool:
        try:
            return self._safe_path(path).exists()
        except FSDriverError:
            return False
